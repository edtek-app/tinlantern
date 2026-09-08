# evals — LLM eval harness + versioned reports (M3/M4): golden questions, groundedness, refusal checks.


## Reports on record

`make evals` writes `reports/m4-llm.md` — the current run, regenerated at
milestone close.

`reports/m4-llm-first-contact.md` is **frozen and never regenerated**. It
is the first time the golden set met a real model, byte-for-byte as
measured: 10 of 18, refusals 6/6, answers 4/12.

It is kept because the run that followed it is much better, and the
improvement came from fixing *this repository's* grounding check, not the
model. Six of those eight failures were defects in our own numeric check
— thousands separators read as two numbers, figures the question itself
supplied treated as fabrications, digits inside a column name read as an
assertion — and one was a reference query encoding a narrower reading of
an ambiguous question than the model chose. None was a model failure; the
model wrote correct SQL for all twelve answerable questions.

Nobody should have to take a later number on trust. Both runs carry their
own provenance header, and the difference between them is the honest
record of what was wrong and where.
