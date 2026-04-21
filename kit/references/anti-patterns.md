# Anti-patterns

Structural failure modes in AI-generated prose. Unlike anti-slop (lexical — specific words and phrases), anti-patterns live at the level of sentence rhythm and paragraph architecture. They survive surface-level editing because they are grammatical, not lexical.

Adapted from [Nous Research's AutoNovel](https://github.com/NousResearch/autonovel) `ANTI-PATTERNS.md`, scoped to Sprawl's link-length unit. The `write.py link --review` command flags the mechanically detectable ones.

---

## The eight patterns that matter for Sprawl

The twelve patterns in AutoNovel's list were written for chapter- and novel-length units. The following eight apply directly to the ≤1000-byte link.

### 1. Over-Explain

The narrator restates what the passage already demonstrated. *"She slammed the door and did not look back. She was angry."* The first sentence is the scene; the second is the narrator explaining the scene to a reader who already saw it. In a 1000-byte link, you do not have bytes for both. Pick.

**Rule:** if the scene shows it, the narrator does not say it.

### 2. Negative-Assertion Repetition

Excessive use of "did not" / "was not" / "could not" constructions. *"He did not look back. He did not speak. He did not move."* The construction is a low-effort way to generate rhythm without committing to what the character *is* doing. It compounds rapidly — two in a paragraph reads as deliberate, four in a link reads as a tic.

**Mechanically flagged** when `--review` counts more than 2 negations in a link-draft. The threshold is per-link; a branch full of links each with 2 negations will still register as a branch tic in the voice report.

### 3. Cataloging-by-Thinking

Reflection compressed into topic-list form. *"It was not a sentry. It was not an ICE. It was not a drone."* Real interiority is messier — it loops, contradicts itself, trails off, lands on an image. A character running down a clean three-item list is rarely a character thinking; it is a narrator imposing thought-shape on a character.

### 4. The Simile Crutch

Overreliance on *"the way a [noun] [verbs] [object]"* or *"like a [noun] [verbs]"*. *"the way a traveller sets a hand on a familiar shoulder"* / *"the way a boy turns when he already knows."* Elegant in isolation, exhausting in accumulation. Most of these similes can be removed entirely without loss.

**Mechanically flagged** when `--review` detects more than 1 instance of the `the way X [does] Y` construction in a link-draft.

### 5. Balanced Antithesis

*"Not X, but Y."* *"It was not an answer. It was a question."* The single most overused LLM rhetorical formula, more common in AI prose than in any human corpus. It homogenizes voice — every character and narrator ends up sounding like they share the same rhetorical training.

**Mechanically flagged** when the phrase "not just X but Y" or tight "Not X. X was Y." patterns appear.

### 6. Triadic Listing

Default grouping by threes. *"The mist, the low drone, the rhythm of lights."* Threes are the fallback when the writer does not have a reason to pick two or four. Two items are often stronger than three.

### 7. Paragraph Rhythm Uniformity

Sentences clustering at the same length throughout the link, usually 4-6 medium sentences. A link where every sentence is the same shape has no rhythm — it has a metronome. Strong prose varies: short, then long, then short. A single long serpentine sentence is fine; three of them in sequence is a pattern.

### 8. Dialogue-as-Written-Prose

When a link contains speech, the speech often arrives as clean written prose — grammatical, complete, without stumbles, interruptions, or age-appropriate imperfection. Real dialogue has false starts, filler, and rhythm that belongs to the speaker. If your character says *"I have been waiting for you for many years, and the waiting has changed me,"* you are writing an essay in character-voice, not dialogue.

---

## The underlying mechanism

LLMs trained on large fiction corpora absorb rhythmic patterns alongside lexical ones. When writing into a fiction context, the easiest output to generate is one that *sounds like* the statistical average of high-quality fiction — which means: balanced sentences, measured cadence, literary-register vocabulary, rhetorical moves that signal "this is a writer writing."

That average output has no distinctive voice. It is the prose a model produces when it has nothing in particular to say and is performing literariness instead. The anti-patterns above are the grammatical signatures of that performance.

The remedy is the same for all of them: **commit to a specific thing.** A named object, a physical action, an unexpected verb, a sentence whose rhythm belongs to this writer at this moment. The patterns collapse as soon as the writing has a reason to be exactly the shape it is.

---

## For Sprawl specifically

Branch-local amplification is the risk unique to this form. If link #30 uses a simile crutch once, it reads as a stylistic move. If links #30–#45 all use the same construction, it has become the branch's voice and new writers arriving will feel pressure to match.

When `read.py context` reports that a branch is doing one of these patterns heavily, treat that as a flag to *refuse* the pattern in your contribution — not as a template to match. Divergence from the branch's rhetorical attractors is usually what the branch needs next.
