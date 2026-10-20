# Terminology

Every DHIS2 option set is published as a `CodeSystem` holding its options as concepts, plus
a `ValueSet` including that whole system - the pair a `#choice` question is answered from.
Concept codes are DHIS2 option UIDs, and the
other DHIS2 identifier rides along on every concept as a property.

## Option sets

| Option set | Concepts | Code fallback |
| --- | ---: | --- |
| [Birth type](CodeSystem-d2-os-Os2aaaaaaaa-cs.html) | 1 | no |
| [Sex](CodeSystem-d2-os-Os1aaaaaaaa-cs.html) | 2 | no |

A "yes" in the fallback column means at least one option in that set has no usable DHIS2
code, or its code was already taken inside the set, so that concept is coded by its DHIS2
UID instead. `d2w fhir validate` names each one.

## Support terminology

Beyond the option sets, the guide publishes the CodeSystems the generated resources code
themselves against:

- [Data elements](CodeSystem-d2-de-cs.html) (`D2DE_CS`) -
  Every DHIS2 data element the generated questionnaires ask for.
- [Form types](CodeSystem-d2-form-type-cs.html) (`D2FormType_CS`) -
  The kind of DHIS2 form a Questionnaire and its responses came from.
- [Period types](CodeSystem-d2-period-type-cs.html) (`D2PeriodType_CS`) -
  The reporting period types the D2Period extension is typed by.
