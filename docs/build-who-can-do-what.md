# Who can do what

**Who this is for:** anyone about to let another person or system reach a middleware.

**Before you start:** [Build a middleware](build-a-middleware.md).

**You will be able to:**

- choose whose rights DHIS2 applies to what your service does
- say what your own login does and does not protect
- give the service's DHIS2 account what it needs and no more

A facade can answer with people's records and write what it is sent, so `mount_facade` makes you say who may. With
nothing stated it refuses to start, because nothing else stops a server you started yourself being reached from another
host.

## The choice

| You pass | Who DHIS2 sees | Captures | Use it when |
| --- | --- | --- | --- |
| `read_as_caller=True` | Each caller, under their own DHIS2 credentials | Posted at once, and refused if DHIS2 refuses that caller | Callers have DHIS2 accounts, and DHIS2's rules should hold for each of them |
| `authentication=your_check` | The one account behind the connection, for everyone your check lets in | Spooled, then drained as that account | Callers have no DHIS2 accounts, and all may do the same things |
| `allow_anonymous=True` | The one account, for anyone who can reach the port | Spooled, then drained as that account | A demo on your own machine |

## `read_as_caller`: DHIS2 decides, per person

```python
mount_facade(app, read_as_caller=True)
```

Each caller presents their own DHIS2 username and password, or a personal access token. DHIS2 checks them, and every read
and capture goes out under them. Measured against DHIS2 2.43.2, with one clerk scoped to a single facility:

| | A person at their facility | A person elsewhere | Listing | Capture at their facility | Capture elsewhere |
| --- | --- | --- | --- | --- | --- |
| `admin` | 200 | 200 | 50 people | | |
| The clerk | 200 | 404 | 1 person | 201 | 409 |

**It trades the spool for DHIS2's own rules.** A spooled capture is posted later, by whoever drains it, and a service
cannot do that as the clerk without keeping the clerk's password. So a capture goes to DHIS2 while the caller waits, and:

- DHIS2 applies every rule, including the ones this facade cannot see: organisation units, sharing, locked periods,
  approvals.
- DHIS2's own record names the clerk, as `storedBy`, rather than the service's account.
- A 201 means DHIS2 has the data, and a refusal stores nothing.
- If DHIS2 is away, the capture fails with a 502 and nothing is stored, so **the client must send it again**.

A deployment that has to take captures while DHIS2 is unreachable needs `authentication=` and the spool, and accepts what
follows from it.

## `authentication=`: your check, one account

```python
mount_facade(app, authentication=your_check)
```

**Your check decides who gets in, and nothing about what they can do.** Everyone it lets through acts as the connection's
account:

- They read whoever that account can see, including people DHIS2 would hide from them personally.
- A clerk limited to one district can submit for another, because the drain posts as that account. Measured against DHIS2
  2.43.2: posted directly, that capture gets a 409 from DHIS2; through a spool and a drain, it is accepted.

So give the connection only what every caller may do. To receive captures and read nobody, add
`reads=False, operations=False`, which answers 404 for `/Patient` and `$summary`.

`/metadata` stays open, because a client has to be able to read the posture it is expected to meet, and says what
`authentication_statement` says. Calling `record_caller(request, username)` from your check is what gives `/whoami` a name
to answer with.

## The service's own account

Whichever you choose, the service holds a connection, and `admin` appears in the examples only because they run against a
local demo instance. Give a real service a dedicated account:

| Under | The connection needs to |
| --- | --- |
| `read_as_caller` | Read metadata, to build the guide as the service starts |
| `authentication=` | Read metadata, read the people every caller may see, and write what every caller may write |

Its password belongs in your deployment's secrets. `Dhis2Client` also takes a personal access token through `PatAuth`:
see [Pluggable auth](https://winterop-com.github.io/dhis2w/architecture/auth/).

## How `d2w fhir serve` differs

`d2w fhir serve` will not bind beyond loopback until a posture is stated. Its
[`dhis2` posture](201-secure.md#under-dhis2-the-register-is-read-as-you) reads as the caller, but it keeps the spool, so
`d2w fhir forward` posts captures as the profile's account and DHIS2 judges that account. Measured against DHIS2 2.43.2,
the clerk above is accepted that way. Give a forwarding account only the write access every submitter should have.

Next: [Choose where the guide comes from](build-choose-a-guide.md).
