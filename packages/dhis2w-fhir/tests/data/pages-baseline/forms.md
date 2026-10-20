# Forms

Every DHIS2 data-capture form this guide publishes is a `Questionnaire`: a data set, an event
program, a tracker program's registration, one stage of a tracker program, or a tracked entity
type's own registration states what is asked, its sections become `#group` items, and each data
element - or, on a registration form, each tracked entity attribute - becomes a question typed
from its DHIS2 value type. The example `QuestionnaireResponse` instances answer these forms on
the very link ids they define.

## Data sets

| Form | DHIS2 id | DHIS2 code | Period type | Sections | Questions |
| --- | --- | --- | --- | ---: | ---: |
| [Mortality &lt; 5 years](Questionnaire-YFTk3VdO9av.html) | `YFTk3VdO9av` | `DS_MORT` | Monthly | 1 | 3 |

### Mortality &lt; 5 years

- Deaths \| reported (2 questions)
- Outside any section (1 questions)

## Event programs

No event programs are selected for this guide.

## Tracker programs

A tracker program publishes one form per capture point: the registration form enrols a person,
asking the program's tracked entity attributes, and each stage is its own form answered once per
event of the enrollment that registration created.

### Child Programme (IpHINAT79UW)

#### Stages

| Form | DHIS2 id | DHIS2 code | Sections | Questions |
| --- | --- | --- | ---: | ---: |
| [Birth](Questionnaire-A03MvHHogjR.html) | `A03MvHHogjR` | `A03MvHHogjR` | 0 | 1 |

### Birth

- Outside any section (1 questions)

## Tracked entity types

No tracked entity types are selected for this guide.
