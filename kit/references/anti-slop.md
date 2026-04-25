# Anti-slop

Universal AI-writing tells. These are lexical and structural patterns statistically overrepresented in LLM output relative to human writing. The `write.py link --review` command scans for them and prints warnings.

The list is adapted from [Nous Research's AutoNovel project](https://github.com/NousResearch/autonovel), specifically their `ANTI-SLOP.md` taxonomy. Credit to that project for the operational form.

---

## Tier 1 — kill on sight

Words statistically overrepresented in LLM output that rarely appear in casual human writing. If any of these appear in your link-draft, rewrite them.

`delve` → dig into · `utilize` → use · `leverage` → use, take advantage of · `facilitate` → help, enable · `elucidate` → explain · `embark` → start · `endeavor` → effort, try · `encompass` → include · `multifaceted` → complex · `tapestry` → describe the actual thing · `testament` → shows, proves · `paradigm` → model, approach · `synergy` → delete and restart · `holistic` → whole, complete · `catalyze` → trigger, cause · `juxtapose` → compare, contrast · `realm` → area, field · `myriad` → many · `plethora` → many

## Tier 2 — suspicious in clusters

Fine individually. Three or more in a single link signals the cadence has gone generic.

`robust` · `comprehensive` · `seamless` · `cutting-edge` · `innovative` · `streamline` · `empower` · `foster` · `enhance` · `elevate` · `optimize` · `scalable` · `pivotal` · `intricate` · `profound` · `resonate` · `navigate` (metaphorical) · `cultivate` · `bolster` · `cornerstone`

## Tier 3 — zero-information filler

Verbal tics with no content. Delete.

"It's worth noting that…" · "Let's dive into…" · "As we can see…" · "In conclusion…" · "Furthermore…" · "In today's world…" · "At the end of the day…" · "When it comes to…"

## Structural red flags

- **Em-dash overload.** More than two per link usually indicates the prose is relying on em-dashes to do the work of punctuation variety. One per link is comfortable; three or more signals over-reliance.
- **"Not just X, but Y" construction.** The most overused LLM rhetorical move. Kill on sight.
- **Sycophantic openings.** "Great question!" "That's an excellent point." Never appropriate in a link.
- **Rigid paragraph template.** Topic sentence → elaboration → example → wrap-up, repeated every time. In a link that's often one paragraph; if every paragraph in a branch follows this shape, something is wrong.
- **Symmetry addiction.** Equal sentence counts, balanced lists of exactly 3 or 5 items, every section the same length.
- **Hedge parade.** Constant "may," "might," "could potentially." If the link is not committing to anything, the link is not doing anything.
- **Said-tag saturation in dialogue.** "He said," "she said" wrapping every line, with both speakers crammed into one paragraph. Structural failure, see `anti-patterns.md` §9.

---

## Fiction-specific tells

These are the AI-writing tells that show up specifically in literary / fiction output. They are more dangerous than the general ones above because they *sound* like good writing. The `--review` scan flags them.

**Kill on sight:**

- "A sense of [emotion]"
- "Couldn't help but feel"
- "The weight of [abstract noun]"
- "The air was thick with [emotion/tension]"
- "Eyes widened" (as default surprise reaction)
- "A wave of [emotion] washed over"
- "A pang of [emotion]"
- "Heart pounded in [his/her] chest" (where else?)
- "[Raven/dark/golden] hair [spilled/cascaded/tumbled]"
- "Piercing [blue/green] eyes"
- "A knowing smile"
- "A sense of unease/dread/foreboding"

These phrases do not describe anything specific. They invoke the *register* of literary prose without committing to a specific image, sensation, or event. They are the single biggest contributor to the "sounds literary, says nothing" failure mode.

When you catch yourself reaching for one of these, ask: what would I be describing if I had to use concrete nouns and verbs? Describe that instead.

---

## The underlying principle

Le Guin's exercise for fantasy writers: **write a description without using a single adjective or adverb.** Force yourself to pick strong nouns and verbs. This is exactly what AI-writing struggles with — the default reach is toward adjective-noun clichés ("ancient wisdom," "piercing gaze," "heavy silence") rather than the specific verb or the unexpected object.

Human writing is lumpy, specific, and surprising. Slop is smooth, general, and predictable.

The job, link by link, is to stay lumpy.
