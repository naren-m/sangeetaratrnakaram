# Sangītaratnākara — structured corpus

Śārṅgadeva's *Saṅgītaratnākara* (13th c.), pulled from **Sanskrit Wikisource**
(`sa.wikisource.org`, CC-BY-SA) and parsed into per-chapter Markdown and a
single structured JSON file.

Retrieved 2026-08-17. Rebuild with:

```bash
python3 -m venv .venv && .venv/bin/pip install requests indic_transliteration
.venv/bin/python scripts/build_corpus.py
```

Raw API responses are cached in `raw/`, so re-runs do not re-fetch.

## Layout

```
raw/                    cached API responses, ASCII-slugged
                        (sangitaratnakara__svaragatadhyaya.wikitext, …)
by-chapter/             01_svaragatadhyaya.md … 08_parishishta_1.md
verses.json             all 4,827 records, structured
review_needed.md        every parsing ambiguity, with context
scripts/build_corpus.py the pipeline
```

## What was built

| # | Chapter | Ślokas | Pāṭākṣara | Divisions | Inferred |
|---|---------|-------:|----------:|----------:|---------:|
| 1 | स्वरगताध्यायः | 604 | 0 | 8 | 2 |
| 2 | रागविवेकाध्यायः | 244 | 0 | 2 | 0 |
| 3 | प्रकीर्णकाध्यायः | 218 | 0 | 1 | 0 |
| 4 | प्रबन्धाध्यायः | 377 | 0 | 1 | 0 |
| 5 | तालाध्यायः | 381 | 16 | 2 | 0 |
| 6 | वाद्याध्यायः | 1224 | 28 | 3 | 1 |
| 7 | नर्तनाध्यायः | 1680 | 0 | 4 | 0 |
| 8 | परिशिष्ट १ | 55 | 0 | 1 | 0 |
| | **Total** | **4783** | **44** | | |

Each `verses.json` record carries: `chapter`, `chapter_num`, `prakarana`,
`prakarana_num`, `verse_num`, `devanagari`, `lines` (hemistichs as they appear
in the source), `slp1`, `iast`, `content_type`, `notation`, `note`,
`source_url`, `retrieved_date`.

SLP1 and IAST are generated with `indic_transliteration`, matching the encoding
used elsewhere in this project (`dhatupatha.tsv` et al.).

## Things worth knowing before you trust this

**The source has no daṇḍas.** The Wikisource text is a transcription of the
Peter Freund e-text, in which daṇḍas were largely stripped. Four of the seven
chapter pages contain no `॥` at all. Verse boundaries are therefore marked by a
**bare trailing Devanāgarī numeral**, not by `॥ N ॥`. Any parser written against
the conventional pattern returns nothing on most of this text.

**A trailing numeral means four different things**, and separating them is the
main correctness risk. The parser distinguishes:

1. a verse number continuing the running count → emitted as a verse;
2. a reset to 1 where the source failed to write a section header → treated as
   an inferred division, flagged;
3. a row index inside embedded notation → excluded (see below);
4. a duplicated or skipped number in the source → the verse is **kept** with
   the source's number and flagged; only genuinely non-verse lines are excluded.

**Embedded musical notation is pervasive and is not verse.** Tālādhyāya carries
prastāra grids (`ऽ ऽ ऽ ऽ` beat rows, `… ॥ मात्रा १`), Rāgavivekādhyāya carries
tab-separated prabandha tables (svara row, song-syllable rows padded with `०`,
then a bare index numeral). These are detected structurally — beat-symbol
tokens, grid labels, tab alignment, and mean token length, since metrical
Sanskrit averages 5.5–8.5 characters per token while svara notation averages
1.4–2.6. Everything excluded is logged with context to `review_needed.md`.

**Only Svaragatādhyāya has prakaraṇa headers.** Chapters 2–7 carry nothing
between the chapter opener and the closing colophon. Divisions there come from
closing colophons (`इति हस्तप्रकरणम्`, which names the section that just
*ended*) or, failing that, from a verse-numbering reset. Divisions marked
`[unmarked division N]` are inferred by the parser and should be checked against
a print edition before being relied on.

**Verse numbering restarts per prakaraṇa in chapter 1 but runs chapter-wide in
chapter 7.** The source is not internally consistent about this. `verse_num` is
always reproduced exactly as the source gives it; nothing is renumbered.

**Pāṭākṣara are tagged, not dropped.** Vādyādhyāya and Tālādhyāya enumerate
named drum strokes (`खुंखुंधरि करगिड … इति कोणाहतः`). These are genuine text
and are numbered in the source, but they are not ślokas and run their own local
numbering. They carry `content_type: "patakshara"` and are excluded from the
verse-numbering gap report.

**The source repeats and skips verse numbers, and the text wins.** Vādyādhyāya
numbers two different ślokas ९३; Wikisource does this in 551 places. An earlier
build dropped those verses to keep its counter consistent, losing about 11% of
the work. They are now kept with the source's own number, carry a
`source numbering anomaly` note, and are barred from moving the counter. Verify
the numbering against a print edition before relying on `verse_num` in
Vādyādhyāya or Tālādhyāya.

**Some ślokas share a line with their svara illustration.** In the alaṅkāra and
tāla sections a verse is followed inline by the notation demonstrating it
(`… तदोद्गीतः सससरिग मममपध`). These are split at the seam: the verse goes to
`devanagari`/`slp1`/`iast`, the illustration is preserved verbatim in
`notation`. The seam is found by svara-token runs, since svara names form a
closed alphabet that ordinary Sanskrit breaks immediately.

**The verse counts do not match the figures in the project plan.** That plan
cited ~170 verses for Svaragatādhyāya and ~1,678 for the whole work; this build
yields 604 and 4,783. Those figures came from secondary literature and were
explicitly flagged as approximate rather than authoritative, so the mismatch is
**reported, not corrected** — no text was dropped or invented to reach a target.
Independent verification against a print edition is the right next step.

## Licensing and scope

Source text is public domain; the Wikisource transcription is CC-BY-SA, so
redistribution of `by-chapter/` and `verses.json` should carry that attribution.

Deliberately **out of scope**: the 1943 Adyar Library edition and the 1978
Shringy & Sharma translation on archive.org. Those are copyrighted critical
editions and were neither fetched nor scraped. Use them as a manual
cross-reference only.

## API etiquette

The fetcher sets a descriptive `User-Agent` with a contact address, sleeps ~1s
between requests, and caches every response, so a rebuild makes zero network
calls.
