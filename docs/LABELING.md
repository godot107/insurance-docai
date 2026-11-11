# How the training labels were made, and how real documents would be labeled

Internal note (not for the public write-up).

## What a Document Intelligence custom model trains on

A custom extraction model learns from example documents in which every value is marked: "this text, in this box on
this page, is the dwelling coverage". The training folder holds, per document, the file itself, its OCR
(`<doc>.pdf.ocr.json`, words and their positions from the prebuilt layout model) and its labels
(`<doc>.pdf.labels.json`: which words belong to which field), plus one `fields.json` listing the fields.

## What this project did (synthetic letters only)

Nobody drew boxes. The letters were generated, so every printed value was known in advance:

1. `src/synth/generate.py` invents a household and policy, prints the letter (6 layouts, 8 fictional insurers) and
   writes the answer key (`samples/synthetic/labels/S###.json`).
2. `src/build_training_set.py` finds each known value in the OCR words (number match, ties broken by distance to the
   field's label text) and writes `labels.json` in the same format Document Intelligence Studio produces.
3. `src/train_custom.py` builds the neural model from that folder (`ins-neural-v1`: 72 letters; `v2`: + 150
   deliberately difficult ones).

Test layouts (C5 postcard, C6 renewal notice) were never uploaded, so every test score is on unseen layouts.
The real letters were **never used for training**, only for checking; their answers are drafts in
`private/labels/L*.json` (status DRAFT until the owner confirms them).

## With real documents (how it would be done in practice)

Real documents have no answer key, so people label them, usually in **Document Intelligence Studio**:

1. Create a custom extraction project pointing at a blob container of sample documents.
2. Studio runs layout OCR; the labeler defines the fields (same names as `schema/fields.yaml`) and **draws a box**
   around each value on each document (or selects the words). Studio writes the same `labels.json` files.
3. Label at least 5 documents per layout (more for neural models and messy scans); include documents where a field
   is *absent*, so the model learns that "not on the document" is an answer.
4. Have a second person check a sample of labels (label errors become model errors); keep a held-out set per layout
   that is never trained on.
5. Train, test on the held-out set, set the review cutoff per layout from those results (RESULTS.md: cutoffs don't
   transfer between layouts), and add newly reviewed documents from the review app back into the training set.

The review app's decisions are already labels in waiting: an accepted or corrected value, with its location from the
model's output, can be turned into a labeled example for the next training run.
