#!/usr/bin/env python3

"""
Mail filter based on header rules
"""

from email.errors import HeaderParseError
from email.header import decode_header
from email.utils import getaddresses
import abc
import argparse
import dataclasses
import enum
import fnmatch
import json
import os
import string
import sys
import typing

try:
    import Milter
except ImportError:  # pragma: no cover
    Milter = None  # type: ignore


CONF_FILE_PATH = '/etc/headermilter.json'


@dataclasses.dataclass
class MessageData:
    """
    Data extracted from a message
    """

    class Item(enum.Enum):
        """
        Data items types
        """

        SUBJECT = 'subject'
        FROM = 'from'
        REPLY_TO = 'reply-to'
        SENDER = 'sender'
        TO = 'to'
        CC = 'cc'
        DEST = 'dest'

        def __repr__(self) -> str:
            return self.value

    ADDR_MAPPING: typing.ClassVar[dict[str, list[Item]]] = {
        'from': [Item.FROM, Item.SENDER],
        'to': [Item.TO, Item.DEST],
        'cc': [Item.CC, Item.DEST],
        'reply-to': [Item.REPLY_TO, Item.SENDER],
    }

    NAMES = ', '.join(repr(option.value) for option in Item)

    # Data items extracted from the message:
    # For address fields (from/to/cc), only the bare adresses are kept
    # dest is the combination of To and CC fields
    # The subject is stored in a list for data type consistency, but the list
    # will always contain exactly one item (possibly empty).
    data: dict[Item, list[str]]

    def __init__(self) -> None:
        self.data = { key: [] for key in self.Item }
        self.data[self.Item.SUBJECT].append('')

    def handle_raw_header(self, key: str, value: str) -> None:
        """
        Parse a raw header from a message and store the extracted data.
        """

        key = key.lower()
        value = self._decode_value(value)

        addr_items = self.ADDR_MAPPING.get(key)
        if addr_items is not None:
            emails = [email for _, email in getaddresses([value]) if email]

            if not emails and value.startswith(('undisclosed-recipients:;',
                'unlisted-recipients:;')):
                emails.append('undisclosed')


            for addr_item in addr_items:
                self.data[addr_item].extend(emails)

            return

        if key == 'subject':
            self.data[self.Item.SUBJECT][0] = value

    @classmethod
    def _decode_value(cls, value: str) -> str:
        """
        Decode the specified header value.
        """

        return ''.join(cls._decoded_parts(value))

    @staticmethod
    def _decoded_parts(value: str) -> typing.Iterable[str]:
        """
        Yield decoded chunks from the specified header value.
        """

        try:
            for raw_chunk, encoding in decode_header(value):
                if isinstance(raw_chunk, str):
                    yield raw_chunk
                else:
                    try:
                        yield raw_chunk.decode(encoding or 'ascii')
                    except (LookupError, UnicodeDecodeError):
                        yield raw_chunk.decode('iso-8859-1', 'replace')
        except HeaderParseError:
            yield value


@dataclasses.dataclass
class Rule:
    """
    Named message filter rule.
    """

    name: str
    message: str
    rule: 'BaseRule'

    VALID_MESSAGE_CHARS: typing.ClassVar[set[str]] = set(string.printable)

    @classmethod
    def from_json(cls, data: 'ConfParser.JSONDict') -> list[typing.Self]:
        """
        Get all rules from the JSON data ('rules' JSON dictionary)
        """

        rules: list[typing.Self] = []

        for name, rule_data in data.dict_contents():
            message = rule_data.get_str('message')

            if not message:
                raise AssertionError(f"Empty message in rule {name}")

            invalid_chars = set(message) - cls.VALID_MESSAGE_CHARS
            if invalid_chars:
                rule_data.raise_error('message', f"Invalid character "
                    f"{invalid_chars.pop()!r} in error message (printable "
                    f"ASCII only)")

            rule = cls.BaseRule.from_json(rule_data)
            rules.append(cls(name, message, rule))

        return rules

    def check(self, data: MessageData) -> str:
        """
        Checks the rule against the specified message data. Returns an error
        message if it matches, an empty string if it does not.
        """

        if self.rule.check(data):
            return self.message

        return ''

    class BaseRule(abc.ABC):
        """
        Base class for rules that examine message data, or combine rule results.
        """

        _classes: typing.ClassVar[dict[str, type['Rule.BaseRule']]] = {}

        def __init_subclass__(cls, *, name: str) -> None:
            """
            Register the rule subclass with the provided name.
            """

            if name:
                cls._classes[name] = cls

        @abc.abstractmethod
        def __init__(self, data: 'ConfParser.JSONDict') -> None:
            """
            Initialize a rule subclass from the provided JSON data.
            """

        @abc.abstractmethod
        def check(self, data: MessageData) -> bool:
            """
            Checks the rule against the specified message data. Returns True iff
            it matches.
            """

        @classmethod
        def from_json(cls, data: 'ConfParser.JSONDict') -> 'Rule.BaseRule':
            """
            Constructs the rule object from JSON data.
            """

            type_str = data.get_str('type')
            try:
                rule_cls = cls._classes[type_str]
            except KeyError:
                valid = ", ".join(cls._classes)
                data.raise_error('type', f"Unknown rule type {type_str!r}. "
                    f"Valid rule types: {valid}")

            return rule_cls(data)

        @abc.abstractmethod
        def rule_repr(self, *, is_root: bool=False) -> str:
            """
            Representation of the rule as user-readable text.
            is_root indicates if the rule is at the root of the object.
            """

        def __repr__(self) -> str:
            return self.rule_repr(is_root=True)

    @dataclasses.dataclass
    class CombinatorialRule(BaseRule, name=''):
        """
        Combination of multiple rules.
        """

        NAME: typing.ClassVar[str] = ''

        rules: list['Rule.BaseRule']

        def __init__(self,  data: 'ConfParser.JSONDict') -> None:
            self.rules = []

            for rule_data in data.get_array('conds'):
                self.rules.append(self.from_json(rule_data))

        def rule_repr(self, *, is_root: bool=False) -> str:
            sub_rules_gen = (rule.rule_repr() for rule in self.rules)
            sub_rules = f' {self.NAME} '.join(sub_rules_gen)

            if is_root:
                return sub_rules

            return f"({sub_rules})"

        def __repr__(self) -> str:
            return self.rule_repr(is_root=True)

    class OrRule(CombinatorialRule, name='or'):
        """
        Disjunction of rules.
        """

        NAME = 'OR'

        def check(self, data: MessageData) -> bool:
            for rule in self.rules:
                if rule.check(data):
                    return True

            return False

    class AndRule(CombinatorialRule, name='and'):
        """
        Conjunction of rules.
        """

        NAME = 'AND'

        def check(self, data: MessageData) -> bool:
            for rule in self.rules:
                if not rule.check(data):
                    return False

            return True

    class NotRule(BaseRule, name='not'):
        """
        Negation of a rule.
        """

        rule: 'Rule.BaseRule'

        def __init__(self,  data: 'ConfParser.JSONDict') -> None:
            rule_data = data.get_dict('rule')
            self.rule = self.from_json(rule_data)

        def check(self, data: MessageData) -> bool:
            return not self.rule.check(data)

        def rule_repr(self, *, is_root: bool=False) -> str:
            return f'NOT {self.rule.rule_repr()}'

    class MatchRule(BaseRule, name='match'):
        """
        Match a property of the message against a pattern.
        """

        item: MessageData.Item
        raw_pattern: str
        pattern: str

        def __init__(self,  data: 'ConfParser.JSONDict') -> None:
            try:
                self.item = MessageData.Item(data.get_str('item'))
            except ValueError:
                data.raise_error('item', f"Unknown match item, expected "
                    f"{MessageData.NAMES}")

            self.raw_pattern = data.get_str('value')
            self.pattern = self.raw_pattern.lower()

        def check(self, data: MessageData) -> bool:
            for item_value in data.data[self.item]:
                if fnmatch.fnmatchcase(item_value.lower(), self.pattern):
                    return True

            return False

        def rule_repr(self, *, is_root: bool=False) -> str:
            return f"{self.item.value} MATCHES {self.raw_pattern!r}"

    class MissingHeadersRule(BaseRule, name='missing-header'):
        """
        Detects missing or empty headers in a message.
        """

        headers: list[MessageData.Item]

        def __init__(self, data: 'ConfParser.JSONDict') -> None:
            self.headers = []

            for raw_ident in data.get_str('headers').replace(',', ' ').split():
                try:
                    header = MessageData.Item(raw_ident)
                except ValueError:
                    data.raise_error('headers', f"Unknown header identifier "
                        f"{raw_ident!r} expected one of {MessageData.NAMES}")

                self.headers.append(header)

            if not self.headers:
                data.raise_error('headers', f"No header identifier specified; "
                    f"expected a list of of {MessageData.NAMES}")

        def check(self, data: MessageData) -> bool:
            for header in self.headers:
                values = data.data[header]
                values_count = len(values)
                if values_count == 0 or (values_count == 1 and not values[0]):
                    return True

            return False

        def rule_repr(self, *, is_root: bool=False) -> str:
            items_str = ', '.join(repr(header) for header in self.headers)
            return f"MISSING HEADER IN {items_str}"


T = typing.TypeVar('T')


class ConfParser(json.JSONDecoder):
    """
    Parser for the JSON configuration
    """

    @dataclasses.dataclass(frozen=True)
    class Configuration:
        """
        Result of the configuration parsing
        """

        sock_path: str
        rules: list[Rule]

    @classmethod
    def parse_file(cls, file_path: str) -> Configuration:
        """
        Parse the specified configuration file from its path.
        Exits if an error occurs.
        """

        try:
            with open(file_path, 'rb') as fdesc:
                json_data = json.load(fdesc, cls=cls)

            return cls._parse_json_data(json_data)
        except (OSError, ValueError) as err:
            sys.exit(f"Unable to load configuration {file_path}: {err}")

    @dataclasses.dataclass
    class JSONContainer:
        """
        Container (dictionary or array) loaded from JSON.
        """

        # These properties are only empty for the root node
        parent: typing.Optional['ConfParser.JSONContainer'] = None
        name_or_idx: str|int = ''

        def raise_error(self, location: str|int, msg: str) -> typing.Never:
            """
            Raise a ValueError indicating the location of the error. The
            provided location is prefixed with the path to this container.
            """

            path = self._append_location(self.get_path(), location)
            raise ValueError(f"{path}: {msg}")

        def get_path(self) -> str:
            """
            Returns the path to this container.
            """

            if self.parent is None:
                return ""

            return self._append_location(self.parent.get_path(),
                self.name_or_idx)

        @staticmethod
        def _append_location(path: str, location: str|int) -> str:
            """
            Adds the provided location to the path.
            """

            if not path:
                # The root path is a dict so its children’ locations cannot be
                # integers
                assert isinstance(location, str), location
                return location

            if isinstance(location, str):
                return f'{path}.{location}'

            return f'{path}[{location}]'

    @dataclasses.dataclass
    class JSONDict(JSONContainer):
        """
        Dictionary loaded from JSON. Provides access to inner attributes.
        """

        data: dict[str, typing.Any] = dataclasses.field(default_factory=dict)

        def dict_contents(self) -> typing.Iterable[tuple[str,
            'ConfParser.JSONDict']]:
            """
            Iterates over the dictionary contents, yielding tuples with the
            key, and the value. Raises a ValueError if a non-JSONDict value
            is encountered.
            """

            for key, value in self.data.items():
                if not isinstance(value, ConfParser.JSONDict):
                    self.raise_error(key, "Expected JSON dictionary {…}")

                yield key, value

        def get_str(self, key: str) -> str:
            """
            Get a non-empty string from the specified dictionary key
            """

            value = self._get_item(key, str, "Expected string")

            if not value:
                self.raise_error(key, "Non-empty string expected")

            return value

        def get_dict(self, key: str) -> 'ConfParser.JSONDict':
            """
            Get a dictionary from the specified dictionary key
            """

            return self._get_item(key, ConfParser.JSONDict,
                "Expected JSON dictionary {…}")

        def get_array(self, key: str) -> 'ConfParser.JSONArray':
            """
            Get an array from the specified dictionary key
            """

            return self._get_item(key, ConfParser.JSONArray,
                "Expected JSON array {…}")

        def _get_item(self, key: str, expected_type: type[T], msg: str) -> T:
            """
            Get an item from the specified dictionary key, checking its type.
            """

            value = self.data.get(key)
            if not isinstance(value, expected_type):
                self.raise_error(key, msg)

            return value

    @dataclasses.dataclass
    class JSONArray(JSONContainer):
        """
        Array loaded from JSON. Provides access to inner items (which all
        need to be JSONDicts).
        """

        data: list['ConfParser.JSONDict'] = dataclasses.field(
            default_factory=list)

        @classmethod
        def build(cls, parent: 'ConfParser.JSONDict', name: str,
            raw_list: list[typing.Any]) -> typing.Self:
            """
            Instantiate a JSONArray from a raw JSON array.
            """

            container = cls(parent, name)
            item_list = container.data
            for idx, item in enumerate(raw_list):
                if not isinstance(item, ConfParser.JSONDict):
                    container.raise_error(idx, "Expected JSON dictionary {…}")

                item.parent = container
                item.name_or_idx = idx
                item_list.append(item)

            return container

        def __iter__(self) -> typing.Iterator['ConfParser.JSONDict']:
            return iter(self.data)

    def __init__(self) -> None:
        super().__init__(object_pairs_hook=self._parse_dict_pairs)

    @classmethod
    def _parse_json_data(cls, json_data: typing.Any) -> Configuration:
        """
        Parse the JSON data loaded from the configuration file.
        """

        if not isinstance(json_data, cls.JSONDict):
            raise ValueError("Expected JSON dictionary {…} at root")

        sock_path = json_data.get_str('socket')
        rules = Rule.from_json(json_data.get_dict('rules'))

        return cls.Configuration(sock_path, rules)

    @classmethod
    def _parse_dict_pairs(cls, pairs: list[tuple[str, typing.Any]]) -> \
        'JSONDict':
        """
        Called during JSON parsing when a dictionary is encountered. Creates a
        JSONDict with the content.
        """

        res = cls.JSONDict()
        res_dict = res.data

        for key, value in pairs:
            if key in res_dict:
                raise ValueError(f"Duplicate key {key!r} found in JSON data")

            if isinstance(value, cls.JSONDict):
                value.parent = res
                value.name_or_idx = key

            elif isinstance(value, list):
                value = cls.JSONArray.build(res, key, value)

            res_dict[key] = value

        return res

if Milter is not None:
    class HeaderMilter(Milter.Base):
        """
        Processes the incoming messages and run the message rules on them.
        """

        def __init__(self, rules: list[Rule]) -> None:
            self._rules = rules
            self._msg_from = ''
            self._rcpt_to = ''
            self._msg_data = MessageData()

        def envfrom(self, addr: str, *_args: str) -> int:
            """
            Handle MAIL FROM command
            """

            if self._msg_from:
                # Second message sent on the connection, reset all variables
                self._rcpt_to = ''
                self._msg_data = MessageData()

            self._msg_from = addr

            return Milter.CONTINUE

        def envrcpt(self, addr: str, *_args: str) -> int:
            """
            Handle RCPT TO command
            """

            self._rcpt_to = addr
            return Milter.CONTINUE

        def header(self, name: str, hval: str) -> int:
            """
            Handle a received header
            """

            self._msg_data.handle_raw_header(name, hval)

            return Milter.CONTINUE

        def eoh(self) -> int:
            """
            Handle the end of headers. Performs the message validation.
            """

            for rule in self._rules:
                reject_msg = rule.check(self._msg_data)
                if reject_msg:
                    self.setreply('550', None, reject_msg.replace('%', '%%'))
                    self._log_decision(f'REJECT {reject_msg}')
                    return Milter.REJECT

            self._log_decision('ACCEPT')
            return Milter.CONTINUE

        def _log_decision(self, decision: str) -> None:
            """
            Log the decision from the currently processed message.
            """

            print(f"From {self._msg_from} to {self._rcpt_to}: {decision}",
                flush=True)
else:
    pass # pragma: no cover


def run(argv: list[str] | None = None) -> None:
    """
    Program entry point
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-c', '--conf', help="Configuration file path",
        default=CONF_FILE_PATH)
    args = parser.parse_args(argv)

    conf = ConfParser.parse_file(args.conf)

    if Milter is None:
        sys.exit("Unable to import the Milter module, please install pymilter")

    Milter.factory = lambda: HeaderMilter(conf.rules)
    Milter.set_flags(0)

    print("Running milter", flush=True)

    os.umask(0o555)
    Milter.runmilter('headermilter', conf.sock_path, timeout=1)


if __name__ == '__main__':
    run()
