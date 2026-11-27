# Voice evaluation set — manifest

Phase 6 brief A4 (branch 3a, `feat/p6-voice-eval`). This file is the
**versioned** half of the Turkish evaluation set: file names, sha256
hashes, the recording condition and the reference transcript. The audio
itself is the owner's own file set and lives **outside the repository**
(default `~/FridayEval/asr/`, mode 0700; `VOICE_EVAL_SET_DIR`) — it is
biometric data and never enters the repo, CI or Friday's database.

How to record, hash and score the set: `docs/runbooks/voice-setup.md` §8.
`manage.py voice_eval` reads the table below:

- `file` — the WAV's name inside the set directory (`[A-Za-z0-9._-]`,
  ending `.wav`; 16 kHz mono 16-bit PCM, at most 30 s).
- `sha256` — the recording's hash, written by `voice_eval --fill-hashes`.
  Empty = not recorded yet (the row is skipped). A file whose hash no
  longer matches is refused: re-recording a clip means clearing its hash
  in the same commit.
- `condition` — `tr-quiet`, `tr-noise`, `cs-quiet`, `cs-noise`, `en-quiet`
  or `en-noise` (language: Turkish, code-switched Turkish/English,
  English; quiet room or room noise such as TV or a fan).
- `reference` — exactly what was said. A literal `|` is written `\|`.
  Scoring normalises case (Turkish `İ/i`, `I/ı`), apostrophes and
  punctuation, so write it naturally.

How the scorer treats what Whisper writes differently (both the reference
and the transcript go through the same rules, `backend/voice/eval/`):

- **Numbers (Turkish rows).** Digits become words, so `3` = `üç` and
  `250` = `iki yüz elli`, either way round. Covered: integers (Turkish
  thousands dots, `1.000.000` = `bir milyon`), decimals with a comma
  (`3,5` = `üç virgül beş`), clock times `HH:MM`/`HH.MM` (`10:30` = `on
  buçuk`, `10:00` = `on`, `09:15` = `dokuz on beş`), percent (`%15` =
  `15%` = `yüzde on beş`) and the lira (`₺250` = `250 TL` = `iki yüz elli
  lira`). A suffix glued to a number keeps its written form (`10'da` =
  `onda`, but `10:30'da` becomes `on buçukda`, not `buçukta`), so **write
  numbers, times, percentages and amounts as digits in the reference**,
  the way Whisper writes them; then both sides always agree. Not covered:
  ordinals (`3.`), dates, ranges, other currencies — write those as you
  expect Whisper to.
- **English rows** compare digits as written: write `3`, not `three`.
- **Hyphens.** A hyphen between two letters is dropped and the parts
  joined (`e-posta` = `eposta`); between a letter and a digit, or with
  spaces around it, it separates words (`Covid-19` = `covid on dokuz`).
- **A failed clip** (unusable WAV, an STT error) scores as an empty
  transcript: all its reference words are deletions.

Targets (A4, initial; any re-set is recorded in the Phase 6 brief, "A9 —
exit criteria record"): WER ≤ 12% `tr-quiet`, ≤ 18% `tr-noise`, ≤ 15%
code-switched (`cs-quiet` + `cs-noise` pooled); English is reported
without a target.

The plan is about 100 Turkish, 50 code-switched and 50 English
utterances, each condition both quiet and with noise, including firm and
project names. The rows below are starting suggestions from the brief;
replace or extend them with the owner's own sentences before recording.

<!-- voice-eval-manifest v1 -->
| file | sha256 | condition | reference |
|---|---|---|---|
| tr-quiet-001.wav |  | tr-quiet | Yarın saat onda diş hekimine gitmem gerekiyor. |
| tr-quiet-002.wav |  | tr-quiet | Bugünkü görevlerimi oku. |
| tr-noise-001.wav |  | tr-noise | Yarın saat onda diş hekimine gitmem gerekiyor. |
| cs-quiet-001.wav |  | cs-quiet | Repo'yu aç. |
| cs-quiet-002.wav |  | cs-quiet | PR'ı merge et. |
| cs-noise-001.wav |  | cs-noise | Deploy'u durdur. |
| en-quiet-001.wav |  | en-quiet | Read me today's tasks. |
