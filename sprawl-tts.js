/* =============================================================
   Sprawl — narration session controller.

   Drives the PLAY affordance on every link meta-line and the
   global PLAY/PAUSE button in the text panel's controls row.

   Lifecycle (one session):
     1) User clicks PLAY on a link
     2) queue = [linkId, ...next links down the visible ancestry]
     3) POST /tts {kind:"link", id} for the first link
     4) Wrap that link's rendered text in .tts-word spans derived
        from the server-supplied plain_text (display) and the
        alignment arrays (timing)
     5) <audio> plays; a rAF loop assigns .speaking/.spoken classes
     6) Prefetch /tts for the next link while the current plays
     7) On `ended`, advance; on queue exhaustion, stop
     8) Tree reselection mid-play → stopSession()

   The session is intentionally tab-local. Reloading the page ends
   any active narration; that matches the spec in
   NARRATION_IMPLEMENTATION.md §2 ("pause persists on same session,
   dies on page load").
   ============================================================= */
(() => {
  "use strict";

  const state = {
    queue:       [],     // ordered link ids still to narrate
    idx:         0,      // current position in queue
    audio:       null,   // HTMLAudioElement for current link
    wordStarts:  null,   // per-word start times (seconds)
    wordEnds:    null,   // per-word end times   (seconds)
    prefetch:    new Map(), // linkId -> Promise<{audio_url, alignment, plain_text}>
    rafId:       null,
    ownerSelectedId: null, // selection snapshot at session start
    origHtmlByLinkId: new Map(), // saved .text-body HTML for restore on stop
  };

  // ----- DOM helpers ----------------------------------------------------

  function $(sel) { return document.querySelector(sel); }

  function linkBlock(linkId) {
    if (!linkId) return null;
    return document.querySelector(`.link-block[data-id="${CSS.escape(String(linkId))}"]`);
  }

  // PAUSE is the "stop the audio" action — we flag it with .is-pause so
  // CSS can render it in var(--fg) instead of the muted grey every other
  // .ctl / .act-* anchor uses. Makes it unambiguous that clicking will
  // interrupt an active playback.
  function setPlayLabel(el, label) {
    if (!el) return;
    el.textContent = label;
    el.classList.toggle("is-pause", label === "PAUSE");
  }

  function setLinkPlayLabel(linkId, label) {
    const block = linkBlock(linkId);
    if (!block) return;
    setPlayLabel(block.querySelector(".act-play"), label);
  }

  function resetAllPlayLabels() {
    // Per-link buttons read LISTEN in their idle state (the top-bar global
    // button keeps the briefer PLAY/PAUSE pairing). setPlayLabel toggles
    // the .is-pause class only when the label === "PAUSE", so any other
    // label — LISTEN here — clears the class cleanly.
    document.querySelectorAll(".act-play").forEach(el => setPlayLabel(el, "LISTEN"));
  }

  function showGlobal(label) {
    const g = $("#tts-global");
    if (!g) return;
    g.style.display = "";
    setPlayLabel(g, label);
  }

  function hideGlobal() {
    const g = $("#tts-global");
    if (!g) return;
    g.style.display = "none";
    // Clearing .is-pause matters even though the button is now display:none:
    // the `#tts-global.is-pause + #tts-bars` selector still matches when
    // the preceding element is hidden, so without this the audiogram keeps
    // animating after playback ends.
    g.classList.remove("is-pause");
  }

  // ----- Queue computation ---------------------------------------------

  // Path from `startId` down to the currently-selected leaf, in reading
  // order. Uses window.ancestryOf (exposed by index.html) — that returns
  // genesis → selected, so we slice from the clicked link to the end.
  function queueFromClicked(startId) {
    if (typeof window.ancestryOf !== "function" || !window.state) return [String(startId)];
    const path = window.ancestryOf(window.state.selectedId) || [];
    const ids = path.map(l => String(l.id));
    const i = ids.indexOf(String(startId));
    if (i < 0) return [String(startId)];
    return ids.slice(i);
  }

  // ----- Word-span rendering -------------------------------------------

  // Replace the link-block's .text-body HTML with plain-text words wrapped
  // in <span class="tts-word" data-tts-idx="N">. Stores the original
  // HTML so stopSession() can restore it (preserving entity underlines
  // and other render-time structure).
  function wrapWordSpans(linkId, plainText) {
    const block = linkBlock(linkId);
    if (!block) return 0;
    const body = block.querySelector(".text-body");
    if (!body) return 0;
    if (!state.origHtmlByLinkId.has(linkId)) {
      state.origHtmlByLinkId.set(linkId, body.innerHTML);
    }
    const words = splitWords(plainText);
    if (!words.length) return 0;
    // Rebuild body with word spans joined by single spaces. Paragraph
    // breaks (double newlines) are preserved as <br><br>.
    const pieces = [];
    let wi = 0;
    const paragraphs = plainText.split(/\n{2,}/);
    paragraphs.forEach((para, pIdx) => {
      if (pIdx > 0) pieces.push("<br><br>");
      const paraWords = splitWords(para);
      paraWords.forEach((w, i) => {
        if (i > 0) pieces.push(" ");
        pieces.push(
          `<span class="tts-word" data-tts-idx="${wi}">${escapeHtml(w)}</span>`
        );
        wi++;
      });
    });
    body.innerHTML = pieces.join("");
    return wi;
  }

  function splitWords(text) {
    return text.split(/\s+/).filter(Boolean);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function restoreOriginalText(linkId) {
    const block = linkBlock(linkId);
    if (!block) return;
    const body = block.querySelector(".text-body");
    if (!body) return;
    const orig = state.origHtmlByLinkId.get(linkId);
    if (orig != null) body.innerHTML = orig;
    state.origHtmlByLinkId.delete(linkId);
  }

  function restoreAllOriginalText() {
    for (const id of Array.from(state.origHtmlByLinkId.keys())) {
      restoreOriginalText(id);
    }
  }

  // ----- Timing: alignment → per-word start/end -----------------------

  // Walk character-level timings; emit a {start, end} for each word.
  // Words are delimited by runs of whitespace. The returned arrays must
  // have length N where N matches `splitWords(plain_text).length` — if
  // they drift, we fall back to playing without highlighting.
  function computeWordTimes(alignment) {
    const chars  = alignment.characters || [];
    const starts = alignment.starts     || [];
    const ends   = alignment.ends       || [];
    const wordStarts = [];
    const wordEnds   = [];
    let curStart = null;
    let lastEnd  = null;
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      const isSpace = /\s/.test(ch);
      if (!isSpace) {
        if (curStart === null) curStart = starts[i] ?? 0;
        lastEnd = ends[i] ?? lastEnd;
      } else if (curStart !== null) {
        wordStarts.push(curStart);
        wordEnds.push(lastEnd ?? (starts[i] ?? 0));
        curStart = null;
        lastEnd  = null;
      }
    }
    if (curStart !== null) {
      wordStarts.push(curStart);
      wordEnds.push(lastEnd ?? (starts[starts.length - 1] ?? 0));
    }
    return { starts: wordStarts, ends: wordEnds };
  }

  // ----- Highlight loop -------------------------------------------------

  function tick() {
    if (!state.audio || state.audio.paused) { state.rafId = null; return; }
    const t = state.audio.currentTime;
    const linkId = state.queue[state.idx];
    const block = linkBlock(linkId);
    if (!block) { state.rafId = requestAnimationFrame(tick); return; }
    const spans = block.querySelectorAll(".tts-word");
    const ws = state.wordStarts, we = state.wordEnds;
    if (!ws || !we || spans.length !== ws.length) {
      // Mismatch — just play audio without highlighting.
      state.rafId = requestAnimationFrame(tick);
      return;
    }
    // Two indices, separately:
    //   speakingIdx — the word being pronounced right now, or -1 if we're
    //                 in a silent gap between words / sentences.
    //   lastDoneIdx — the highest index whose end time has passed. These
    //                 words stay .spoken even during silences — without
    //                 this, a finished word would lose its class during
    //                 inter-sentence pauses and visually "unreveal" until
    //                 the next word started.
    // Linear scan is fine at link length; binary search would be overkill.
    let speakingIdx = -1;
    let lastDoneIdx = -1;
    for (let i = 0; i < ws.length; i++) {
      if (t < ws[i]) break;
      if (t <= we[i]) { speakingIdx = i; break; }
      lastDoneIdx = i;
    }
    for (let i = 0; i < spans.length; i++) {
      const s = spans[i];
      if (i === speakingIdx) {
        s.classList.add("speaking");
        s.classList.remove("spoken");
      } else if (i <= lastDoneIdx) {
        s.classList.add("spoken");
        s.classList.remove("speaking");
      } else {
        s.classList.remove("spoken", "speaking");
      }
    }
    state.rafId = requestAnimationFrame(tick);
  }

  // ----- Fetch + play ---------------------------------------------------

  async function fetchTts(linkId) {
    // Use SprawlAPI.tts — always kind="link" in v1 (spec is homepage-only).
    try {
      return await SprawlAPI.tts("link", String(linkId));
    } catch (e) {
      console.warn("tts fetch failed for link", linkId, e);
      return null;
    }
  }

  async function playCurrent() {
    const linkId = state.queue[state.idx];
    if (!linkId) { stopSession(); return; }

    // Guard: the user changed selection while we were advancing.
    if (window.state && state.ownerSelectedId !== window.state.selectedId) {
      stopSession(); return;
    }

    let payload = state.prefetch.get(linkId);
    if (!payload) payload = fetchTts(linkId);
    state.prefetch.delete(linkId);
    const resolved = await payload;
    if (!resolved || !resolved.audio_url || !resolved.plain_text) {
      // Nothing to narrate; skip.
      advance(); return;
    }
    // Second guard: the await may have taken a second; check again.
    if (window.state && state.ownerSelectedId !== window.state.selectedId) {
      stopSession(); return;
    }

    // Render word spans using the server's plain text (display) and
    // compute per-word timings from the alignment characters (timing).
    const wordCount = wrapWordSpans(linkId, resolved.plain_text);
    const times = computeWordTimes(resolved.alignment || {});
    state.wordStarts = times.starts;
    state.wordEnds   = times.ends;
    if (wordCount !== times.starts.length) {
      // Alignment/plain_text drift — leave spans rendered but disable
      // highlighting (tick() short-circuits on count mismatch).
      console.warn("tts alignment/word drift", { wordCount, timed: times.starts.length });
    }

    // Swap the audio element.
    if (state.audio) { state.audio.pause(); state.audio.src = ""; state.audio = null; }
    const audio = new Audio(resolved.audio_url);
    audio.preload = "auto";
    audio.addEventListener("ended", onAudioEnded);
    state.audio = audio;

    try {
      await audio.play();
    } catch (e) {
      // Autoplay blocked, network error, etc. Treat as stop so we don't
      // leave UI in mid-play state.
      console.warn("tts audio.play() rejected", e);
      stopSession();
      return;
    }

    resetAllPlayLabels();
    setLinkPlayLabel(linkId, "PAUSE");
    showGlobal("PAUSE");
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = requestAnimationFrame(tick);

    // Kick off prefetch of the next link so playback can chain cleanly.
    const nextId = state.queue[state.idx + 1];
    if (nextId && !state.prefetch.has(nextId)) {
      state.prefetch.set(nextId, fetchTts(nextId));
    }
  }

  function onAudioEnded() {
    // Leave the just-finished block restored before advancing so the
    // previously-highlighted text flicks back to its rendered form as
    // the next block takes focus.
    const finishedId = state.queue[state.idx];
    if (finishedId) restoreOriginalText(finishedId);
    advance();
  }

  function advance() {
    state.idx++;
    if (state.idx >= state.queue.length) { stopSession(); return; }
    playCurrent();
  }

  function pauseSession() {
    if (!state.audio) return;
    state.audio.pause();
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = null;
    const cur = state.queue[state.idx];
    // Per-link button uses LISTEN in its idle/paused state; the global
    // panel-centre button sticks to the shorter PLAY.
    if (cur) setLinkPlayLabel(cur, "LISTEN");
    showGlobal("PLAY");
  }

  function resumeSession() {
    if (!state.audio) return;
    state.audio.play().catch(() => {/* user gesture required, rare */});
    const cur = state.queue[state.idx];
    if (cur) setLinkPlayLabel(cur, "PAUSE");
    showGlobal("PAUSE");
    if (state.rafId) cancelAnimationFrame(state.rafId);
    state.rafId = requestAnimationFrame(tick);
  }

  function stopSession() {
    if (state.audio) {
      try { state.audio.pause(); state.audio.src = ""; } catch {}
      state.audio = null;
    }
    if (state.rafId) { cancelAnimationFrame(state.rafId); state.rafId = null; }
    restoreAllOriginalText();
    state.queue = [];
    state.idx = 0;
    state.wordStarts = null;
    state.wordEnds = null;
    state.prefetch.clear();
    state.ownerSelectedId = null;
    resetAllPlayLabels();
    hideGlobal();
  }

  // ----- Public entry points -------------------------------------------

  function onLinkButtonClick(linkId) {
    linkId = String(linkId);
    const currentId = state.queue[state.idx];
    // If this is the currently-playing link, flip play/pause.
    if (currentId === linkId && state.audio) {
      if (!state.audio.paused) return pauseSession();
      return resumeSession();
    }
    // Else: stop any current session and start a fresh queue here.
    stopSession();
    if (!window.state || !window.state.selectedId) return;
    state.queue = queueFromClicked(linkId);
    state.idx = 0;
    state.ownerSelectedId = window.state.selectedId;
    playCurrent();
  }

  function onGlobalClick() {
    if (!state.audio) return;
    if (state.audio.paused) resumeSession();
    else pauseSession();
  }

  function bindGlobal() {
    const g = document.getElementById("tts-global");
    if (g) g.addEventListener("click", (e) => { e.preventDefault(); onGlobalClick(); });
    startBarsLoop();
  }

  // Audiogram motion. Runs a persistent rAF loop; the three bars only
  // have visible heights when #tts-global.is-pause is set (i.e., audio
  // is actively playing). Heights come from summing three non-harmonic
  // sine waves per bar, with a different phase offset per bar. The
  // non-harmonic frequencies mean the combined pattern doesn't repeat
  // at any short period — it reads as organic motion rather than a
  // looping keyframe. Still cheap: three trig calls × three bars per
  // frame. Skipping the DOM write entirely when the button isn't in
  // the pause state keeps the loop idle-cheap when no one's listening.
  function startBarsLoop() {
    const bars = document.querySelectorAll("#tts-bars > span");
    if (bars.length !== 3) return;
    const global = document.getElementById("tts-global");
    // One independent sine per bar at a mutually non-harmonic rate so the
    // three tops never lock into a repeating formation. A small secondary
    // harmonic per bar (slightly above the base rate) adds flutter so
    // motion doesn't look like a pure wave.
    const BARS = [
      { base: 5.3, flutter: 11.1, phase: 0.0 },
      { base: 7.9, flutter:  9.3, phase: 1.7 },
      { base: 3.7, flutter: 13.1, phase: 3.1 },
    ];
    function frame() {
      requestAnimationFrame(frame);
      if (!global || !global.classList.contains("is-pause")) return;
      const t = performance.now() / 1000;
      for (let i = 0; i < 3; i++) {
        const b = BARS[i];
        // 70% of amplitude from the base sine (clear bar movement),
        // 30% from faster flutter (visual liveness).
        const base    = Math.sin(t * b.base    + b.phase) * 0.5 + 0.5;
        const flutter = Math.sin(t * b.flutter + b.phase * 1.3) * 0.5 + 0.5;
        const mix = base * 0.7 + flutter * 0.3;
        // Floor at 0.15em so the bar is always visible; 1em ceiling tracks
        // the PAUSE text's height.
        bars[i].style.height = (0.15 + mix * 0.85).toFixed(3) + "em";
      }
    }
    requestAnimationFrame(frame);
  }

  // Exposed so the homepage's selectLink() can stop narration when the
  // reader picks a different branch of the tree.
  window.SprawlTTS = { onLinkButtonClick, stopSession };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindGlobal);
  } else {
    bindGlobal();
  }
})();
