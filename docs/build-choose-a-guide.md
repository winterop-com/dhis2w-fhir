# Choose where the guide comes from

**Who this is for:** the developer deciding what a middleware loads as it starts.

**Before you start:** [Build a middleware](build-a-middleware.md).

**You will be able to:**

- open a facade off a live instance, a compiled guide, or a published package
- say what each one needs, and what it costs
- open a facade with no network at all

The guide is the published contract: which forms exist, what their questions are, which codes they take. Everything else
a facade does reads it. `open_facade` loads it once, as the service starts, and holds it for the life of the process.

| Where | You give it | It needs | Choose it when |
| --- | --- | --- | --- |
| Live off the instance | `FacadeConfig(canonical, root)` and a `client` | A DHIS2 to read as it starts | You want to start now, with no build step |
| A published package | `FacadeConfig(canonical, root, package=Path("package.tgz"))` | The package file | Somebody publishes the guide you build against |
| A compiled guide | `FacadeConfig(canonical, root)`, with the guide at `root/ig/fsh-generated/resources` | The compiled files | You build the guide yourself with [`d2w fhir generate`](201-generate.md) |
| A project on disk | `load_project(directory)`, from `dhis2w_fhir_engine.facade.config`, in place of the settings | A `fhir.toml` | You already run a `d2w fhir` project |

## Live off the instance

```python
config = FacadeConfig(canonical="http://example.org/fhir/demo", root=Path(".facade"))

async with Dhis2Client(base_url=url, auth=auth) as client:
    async with open_facade(config, client=client) as facade:
        print(len(facade.forms()), "forms published")
```

With no compiled guide under `root`, `open_facade` builds the same guide off the instance, using the builders
`d2w fhir serve --live` uses. It reads the instance's metadata as it starts, so **a form changed in DHIS2 appears when the
service restarts**.

## A guide off disk

With a compiled guide or a package, `open_facade` reads that and takes no client:

```python
config = FacadeConfig(canonical="http://example.org/fhir/demo", root=Path(".facade"), package=Path("package.tgz"))

async with open_facade(config) as facade:
    validated = facade.validate_capture(body)
    result = facade.translate(response)
```

Validation and translation then run with no network at all, so this half is testable with no DHIS2 anywhere near it. A
call that needs DHIS2, such as `post` or `drain`, refuses with `NoDhis2ConnectionError`. With neither a guide nor a client,
`open_facade` refuses with `CompiledIgMissingError` rather than open an empty facade.

**A client alongside a guide off disk** reads the one fact a compiled guide cannot carry: R4 spells `BOOLEAN` and
`TRUE_ONLY` as the same `#boolean` item type, so the value types behind the questions come from the instance.

## The settings

| `FacadeConfig` field | What it is |
| --- | --- |
| `canonical` | The guide's canonical URL, which every form and extension is named under. Must match the guide when you read one off disk |
| `root` | The directory holding the compiled guide and the spool |
| `package` | A published guide's `package.tgz`, unpacked under `root` on opening |
| `generate` | The naming and identifier tokens the guide was generated under. The defaults are what `d2w fhir init` writes |
| `coded_answer_mode` | How a coded answer not spelled as the published concept code is read when translating |

Next: [Receive captures](build-receive-captures.md).
