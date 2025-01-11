Header Milter
=============

This implements a simple [milter](https://en.wikipedia.org/wiki/Milter) (mail
filter) that rejects mail whose headers match certain conditions.

- Match the mail Subject with a case-insensitive pattern
- Match the address part of the address (without the name) in From, To, Cc, or
  Reply-To headers with a case-insensitive pattern. If the headers contains
  multiple addresses, any address can match. It is also possible to match
  a receiver address (any address in To, Cc) or a sender address (any address in
  From or Reply-To).
- Missing or empty headers
- Combine checks with boolean operators (AND, OR, NOT)

My personal use case is with email addresses that I use exclusively to subscribe
with mailing list: Any mail that I receive on these addresses which is not also
Cc-ed to the mailing list is probably spam and can be rejected.

**Note on address headers**: Mail addresses in header are validated, and removed
from consideration if they are not properly formatted. A To header which only
contains invalid addresses will trigger the “missing/empty header” rule.

There is an exception for “undisclosed recipients” headers that are created by
some email clients and mailing lists. Specifically, if an address headers starts
with `undisclosed-recipients:;` or `unlisted-recipients:;` and does not contains
other valid addresses, the milter will act as if the header contains the address
`undisclosed`. This address can be matched to detect such emails.

Installation
------------

Only the `headermilter.py` file is required. Run it to start the service. The
configuration is loaded from `/etc/headermilter.json` (can be changed with the
`-c` command-line switch)

The service will create a Unix socket for connection with the MTA. The socket
is world-writable so it should be placed in a directory with restricted
permissions.

The service only needs permission to read its configuration (which does not
contain sensitive data so it may be world-readable) and to create its socket.

Here is an example of installation for use with Postfix:

Create a dedicated user to run `headermilter`:

```
useradd -d / -M -r headermilter -s /bin/false
```

Install `headermilter.py` into `/srv/headermilter/`

Create `/etc/headermilter.json` with the configuration (see below for details).
The socket path will be `/var/spool/postfix/headermilter/headermilter.sock`
(Postfix runs in a chroot so the socket needs to be in `/var/spool/postfix/`)

Create the socket directory and set its permissions:

```
mkdir -m 0750 /var/spool/postfix/headermilter/
chown headermilter:postfix /var/spool/postfix/headermilter/
```

Example service file:

```
# /etc/systemd/system/headermilter.service
[Unit]
Description=Header Milter

[Service]
Type=simple
ExecStart=/srv/headermilter/headermilter.py
User=headermilter

[Install]
WantedBy=multi-user.target
```

Postfix configuration:

```
# /etc/postfix/main.cf
[...]
smtpd_milters = unix:headermilter/headermilter.sock
```

Configuration
-------------

The configuration file is in JSON format. Here are the attributes of the
configuration objects.

### JSON root

- `socket`: String; path to the socket to create
- `rules`: Dictionary; the keys are identifiers for each rule, and the values
  are dictionaries for each root rule.

### Rules

- `type`: String; the type of the rule, can be either `not`, `and`, `or`,
  `match`, `missing-header`. See below for attributes specific for each rule
  type.
- `message`: String; only for root rules, not rules which are children of other
  rules. Indicates the rejection message to use when the rule matches.

### NOT rule

Negates the result of a sub-rule.

- `rule`: Dictionary; the sub-rule to negate

### AND rule

Combines multiple sub-rules; matches if all the sub-rules match.

- `conds`: List of dictionaries; the sub-rules to combine

### OR rule

Combines multiple sub-rules; matches if any the sub-rules match.

- `conds`: List of dictionaries; the sub-rules to combine

### MATCH rule

Match a header against a pattern.

- `item`: String; header item to check. Can be either `subject`, `from`,
  `reply-to`, `sender`, `to`, `cc`, `dest`.
- `value`: The pattern to match against. The match is case-insensitive and the
  pattern can contain `*` (0 or more any character), `?` (any character),
  `[seq]` (any character in `seq`), `[!seq]` (any character not in `seq`).

### MISSING HEADER rule

Detects missing or empty headers.

- `headers`: String; comma or space-separated of header items to check, in
  `subject`, `from`, `reply-to`, `sender`, `to`, `cc`, `dest`. If any of the
  specified header items is missing/empty, the rule is triggered.

### Example

A configuration example can be found in
[tests/data/conf.json](tests/data/conf.json).

Logging
-------

`headermilter.py` logs accept/reject decisions to standard output. Redirect them
at your convenience.
