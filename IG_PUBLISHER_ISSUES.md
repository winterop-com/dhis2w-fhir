# HL7 IG Publisher issues

Issues in the HL7 FHIR IG Publisher (`publisher.jar`, [HL7/fhir-ig-publisher](https://github.com/HL7/fhir-ig-publisher)), not in DHIS2. Each one shapes what
`d2w fhir generate` may publish or what a build of its output prints, and each repro starts from metadata
a DHIS2 instance legitimately holds. The numbers are the ones these issues carry in the dhis2w host's
[`DHIS2_ISSUES.md`](https://github.com/winterop-com/dhis2w/blob/main/DHIS2_ISSUES.md) sequence, kept so
existing references stay valid.

## How to report

One group is one upstream report, filed as an issue on HL7/fhir-ig-publisher. Copy the group's
**Summary** as the description and its **Versions affected** line under it, then add each member issue
below: its heading as a sub-title, then its Repro, Expected and Actual verbatim. Once filed, put the link
in the group's **Reported** line and in the status table's Reported column.

## Status by group

| Group | Issues | Publisher versions observed | Open | Reported |
| --- | --- | --- | --- | --- |
| [HL7 IG Publisher](#hl7-ig-publisher) | [#103](#103-the-ig-publisher-writes-a-resources-title--text--display-into-its-final-markdown-pass-without-escaping--and-dies-re-parsing-the-page-it-just-wrote), [#107](#107-the-ig-publishers-concept-anchor-slug-strips-whitespace-so-two-distinct-codes-render-one-duplicate-anchor-id), [#132](#132-the-ig-publisher-builds-package-combinedtgz-from-outputpackagetgz-before-it-has-written-that-runs-package) | 2.3.2, 2.3.4 | 3 | not yet |

## HL7 IG Publisher

**Summary.** The IG Publisher mishandles content that a DHIS2-derived guide carries legitimately, and orders one build step before its input exists. It writes a resource's `title`, `text` or `display` into its final markdown pass without escaping `<`, then dies re-parsing the page it wrote; its concept anchor slug strips whitespace, so two distinct codes such as `Pre eclampsia` and `Preeclampsia` render one duplicate anchor id; and it builds `package-combined.tgz` from `output/package.tgz` before it has written that run's package, so the combined package is stale or missing. A guide generated from real national metadata fails to build, or builds with broken anchors and a stale package.

**Versions affected:** HL7 `fhir-ig-publisher` 2.3.2 (#103, #107) and 2.3.4 (#132), FHIR R4.

**Reported:** not yet.

**Issues:** [#103](#103-the-ig-publisher-writes-a-resources-title--text--display-into-its-final-markdown-pass-without-escaping--and-dies-re-parsing-the-page-it-just-wrote), [#107](#107-the-ig-publishers-concept-anchor-slug-strips-whitespace-so-two-distinct-codes-render-one-duplicate-anchor-id), [#132](#132-the-ig-publisher-builds-package-combinedtgz-from-outputpackagetgz-before-it-has-written-that-runs-package).

### 103. The IG publisher writes a resource's `title` / `text` / `display` into its final markdown pass without escaping `<`, and dies re-parsing the page it just wrote

**What a caller is trying to do.** Publish a guide whose resource names come from a national DHIS2
instance. DHIS2 names carry `<` legitimately and everywhere: an age band is `5 to < 15 years,
Female`, a disaggregation cell is `Male, <15y`, an indicator is `Mortality < 5 years by gender`.
None of that is a defect in the instance, and a guide that repeats its instance's names byte for
byte is the guide people asked for.

**Version observed:** HL7 `fhir-ig-publisher` 2.3.2 (`publisher.jar`, `releases/latest` as of
2026-08), FHIR R4, running against a guide generated from DHIS2 2.42 metadata.

**Status (2026-09-07):** not part of this sweep (HL7 IG publisher, not DHIS2); `make verify-igs` was not run.

**Status (2026-09-11):** not retested; the HL7 IG publisher is not DHIS2 and is outside this sweep's targets.

**Repro.** Any R4 resource whose `title`, `item.text`, or `concept.display` holds a bare `<`,
published through a normal build:

```bash
mkdir -p ig/input/resources
cat > ig/input/resources/CodeSystem-repro.json <<'JSON'
{
  "resourceType": "CodeSystem",
  "id": "repro",
  "url": "http://example.org/fhir/CodeSystem/repro",
  "name": "Repro_CS",
  "title": "Age bands",
  "status": "draft",
  "content": "complete",
  "concept": [ { "code": "band-1", "display": "5 to < 15 years, Female" } ]
}
JSON

# then, from the IG root
java -Xmx4g -jar publisher.jar ig.ini -ig .
```

**Expected.** The publisher HTML-escapes the strings it writes into the pages it generates, the way
it escapes the same strings everywhere else in the run, and the build finishes. A `<` in a display
name is data, not markup: no other pass in the run treats it as markup.

**Actual.** The build runs to completion - every resource validated, every page rendered, "Checking
Output HTML" passed - and then dies in the final markdown pass:

```text
Publishing Content Failed: Unable to process page CodeSystem-repro.html
Caused by: org.hl7.fhir.exceptions.FHIRFormatError: Unable to Parse HTML - node 'p' has
  unexpected content: ... last text = '5 to '
  at org.hl7.fhir.igtools.publisher.utils.AIProcessor.produceMDForResource(...)
```

Three things make this expensive rather than merely wrong:

1. **It is the last pass.** On a national guide that is hours of validation and rendering spent
   before the failure, every time.
2. **The message names a page, not the object.** `CodeSystem-repro.html` is what the publisher was
   writing; which of the several hundred DHIS2 objects on that page carried the character is left to
   the reader.
3. **Earlier passes do not see it.** The same string passes FHIR validation and passes the
   publisher's own "Checking Output HTML" step, so nothing before the end of the run reports it.

The same character in an `identifier[].value` fails a little earlier and a little differently -
`Unable to Parse HTML - node 'td' has unexpected content`, from the identifier table cell, which the
publisher also writes raw. A `>` and a bare `&` are tolerated by the parse and render as a malformed
page rather than an aborted build, so `<` is the one character seen to be fatal.

**Workaround applied in the dhis2w-fhir pack.** Three, at three distances from the publisher:

- `d2w fhir generate` refuses a run whose selected DHIS2 names or codes carry `<`, naming the object
  before a file is written - `_refuse_build_aborting_objects` and its siblings in
  [`packages/dhis2w-fhir/src/dhis2w_fhir/service.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/service.py), over the
  `build_aborting_name` / `build_aborting_code` predicates in
  [`packages/dhis2w-fhir/src/dhis2w_fhir/validation/__init__.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/validation/__init__.py).
- `d2w fhir generate --substitute-hostile-names` publishes the name in wording the publisher
  survives instead - `5 to under 15 years, Female` - leaving DHIS2 and every emitted identifier
  untouched: [`packages/dhis2w-fhir/src/dhis2w_fhir/validation/substitution.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/validation/substitution.py) and
  [`packages/dhis2w-fhir/src/dhis2w_fhir/hostile_names.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/hostile_names.py). This exists because refusing is the wrong
  answer for an instance whose age bands are all named this way.
- `d2w fhir check-artifacts` applies the same predicates to the files already on disk, which is what
  `make build` runs first:
  [`packages/dhis2w-fhir/src/dhis2w_fhir/validation/artifacts.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/validation/artifacts.py).

**How to know it's fixed:** a guide holding the repro CodeSystem builds to completion, and the
rendered page shows `5 to &lt; 15 years, Female`. The refusal and the rewrite both become optional
at that point.

**Verifier:** none yet.

---

### 107. The IG publisher's concept anchor slug strips whitespace, so two distinct codes render one duplicate anchor id

**What a caller is trying to do.** Publish a `content: complete` CodeSystem enumerating every
option code a national instance holds. DHIS2 option codes are free text, and an instance
legitimately holds both `Pre eclampsia` and `Preeclampsia` as distinct codes on different
option sets. A complete CodeSystem must state both - dropping either would claim a code the
guide's ConceptMaps still reference.

**Version observed:** HL7 `fhir-ig-publisher` 2.3.2 (`publisher.jar`), FHIR R4, rendering a
CodeSystem generated from DHIS2 2.43 metadata.

**Status (2026-09-07):** not part of this sweep (HL7 IG publisher, not DHIS2); `make verify-igs` was not run.

**Status (2026-09-11):** not retested; the HL7 IG publisher is not DHIS2 and is outside this sweep's targets.

**Repro.** A CodeSystem holding two concepts whose codes differ only in whitespace:

```json
{"resourceType": "CodeSystem", "id": "anchor-repro", "status": "active",
 "content": "complete", "url": "http://example.org/anchor-repro",
 "concept": [
   {"code": "Pre eclampsia", "display": "Pre eclampsia"},
   {"code": "Preeclampsia", "display": "Preeclampsia"}
 ]}
```

Build the guide. The rendered `CodeSystem-anchor-repro.html` carries the same anchor id for
both concept rows, and QA reports it - on the real guide, as:

```text
Internal error in location for message: 'Error @1, 2: Found / expecting a token name',
loc = '.../CodeSystem-d2-option-code-id-cs.html',
err = 'The html source has duplicate anchor Ids: d2-option-code-id-cs-Preeclampsia'
```

**Expected.** The publisher owns its anchor scheme; two distinct codes should render two
distinct anchors (an ordinal suffix on the collision would do), or the QA message should name
the two source codes rather than reporting an internal error against a location the message
itself calls malformed.

**Actual.** One anchor id for both rows, a QA error per collision, and an `Internal error in
location` line above it. The build still exits 0 - the errors are QA-level.

**Workaround applied in the dhis2w-fhir pack:** posture-dependent, and never a merge. Two codes that differ
only in whitespace stay two concepts either way - merging them would make a `content: complete`
claim false, and `_distinct_concepts` in
[`packages/dhis2w-fhir/src/dhis2w_fhir/resources/identifier_terminology.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/resources/identifier_terminology.py) still dedupes exact
duplicates only.

Under `hostile_names = "substitute"` the run publishes no space-carrying code at all:
[`packages/dhis2w-fhir/src/dhis2w_fhir/hostile_names.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/hostile_names.py) hyphenates every space in a DHIS2 code
before emission ([`packages/dhis2w-fhir/src/dhis2w_fhir/coded.py`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/coded.py) holds the rewrite and the
de-collision ordinal), so `Pre eclampsia` is published as `Pre-eclampsia`, `Preeclampsia` keeps
its own spelling, and the two anchor ids differ. The DHIS2 code rides along byte-true as a
`dhis2-code` concept property, so nothing about the join back to the instance is lost.

Under `hostile_names = "refuse"` and with the key unset, the codes reach the guide byte-true and
the QA errors remain - cosmetic, and counted among a guide's expected errors.
[the dhis2w-fhir `201-troubleshooting` page](https://winterop-com.github.io/dhis2w-fhir/201-troubleshooting/) names the symptom and both postures.

---

### 132. The IG publisher builds `package-combined.tgz` from `output/package.tgz` before it has written that run's package

**Version observed:** HL7 `fhir-ig-publisher` 2.3.4 (Git# 7ae92f79415a), FHIR R4, 2026-09-25, on every
guide and registry package this toolchain builds.

**Repro.** Any guide, built into an `output/` directory that does not exist yet:

```bash
rm -rf output temp
java -Xmx8g -jar publisher.jar ig.ini -ig .
grep -n "combined package" <the run's log>
```

```text
Generating combined package
Error generating combined package: /home/publisher/work/output/package.tgz (No such file or directory)
```

**Expected.** The combined package is generated from the package this run built, after that package
is written - or the step reads the package from wherever the run holds it at that point.

**Actual.** `PublisherGenerator.genCombinedPackage()` runs unconditionally after "Reclaiming memory..."
and before Jekyll renders the site. It opens `<outputDir>/package.tgz` with a `FileInputStream`, which
at that point holds no package from this run: on a fresh `output/` the open fails, is caught, and is
logged as the `Error` above; the run then continues, writes `output/package.tgz` as usual, and exits
0. Only `package-combined.tgz` - the package plus the expansions and terminology it uses - is never
produced. Read from the bytecode: the call site is `generate(...)` offset 1949, the read is
`genCombinedPackage()` offset 164-170, and there is no `ig.ini` or IG parameter guarding it.

The fresh-directory case is the loud one. On a build over an `output/` a previous run left behind,
the same step reads that **previous** run's `package.tgz` and writes a combined package from stale
content without a word.

**Workaround applied in the dhis2w-fhir pack.** None is possible from outside the publisher, and nothing here
reads `package-combined.tgz`. The scaffolded Makefile's `REPORT_RUN`
([`packages/dhis2w-fhir/src/dhis2w_fhir/scaffold/templates/Makefile.jinja`](https://github.com/winterop-com/dhis2w-fhir/blob/main/packages/dhis2w-fhir/src/dhis2w_fhir/scaffold/templates/Makefile.jinja)) notices the line in a
build that exited 0 and prints a note saying what it is, so it is not read as a failed build. The
copy-in `make build` never carries `output/` into the container, so it always takes the loud path
rather than the stale one.

---
