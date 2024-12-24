#!/usr/bin/env -S python3 -B

"""
Display details on the configured rules and tests them on specified provided
mail files.
"""

from email.errors import MultipartInvariantViolationDefect
from email.message import Message
from email.parser import HeaderParser
import argparse
import sys
import typing

from headermilter import ConfParser, Rule, CONF_FILE_PATH, MessageData


class RawHeaderCollector(Message):
    """
    Collects the headers from the message as they are generated.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        super().__init__(*args, **kwargs)
        self.headers: list[tuple[str, str]] = []

    def set_raw(self, name: str, value: str) -> None:
        self.headers.append((name, value))
        super().set_raw(name, value)


def get_message_data(mail_file: str) -> MessageData:
    """
    Get message data from a mail file.
    """

    with open(0 if mail_file == '-' else mail_file,
        encoding='iso-8859-1') as fdesc:
        collector = HeaderParser(RawHeaderCollector).parse(fdesc)

    defects = [defect for defect in collector.defects
        if not isinstance(defect, MultipartInvariantViolationDefect)]

    if defects:
        defect_list = ", ".join(repr(defect) for defect in defects)
        sys.exit(f"{mail_file} has defects: {defect_list}")

    message_data = MessageData()
    for name, value in collector.headers:
        message_data.handle_raw_header(name, value)

    return message_data


def show_mail_file_info(mail_file: str, rules: list[Rule]) -> None:
    """
    Parse the mail file, extract the mail header data, display it, then display
    the rules that match.
    """

    message_data = get_message_data(mail_file)

    for item in MessageData.Item:
        item_values = ', '.join(repr(value)
            for value in message_data.data[item])
        print(f"   {item.value.title()}: {item_values}")

    print("   ")

    for rule in rules:
        result = rule.check(message_data) or 'no match'
        print(f"   {rule.name}: {result}")


def run() -> None:
    """
    Program entry point
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-c', '--conf', help="Configuration file path",
        default=CONF_FILE_PATH)
    parser.add_argument('-m', '--matching', help="Only show matching files",
        action='store_true')
    parser.add_argument('mail_file', nargs='*', default=['-'],
        type=str)
    args = parser.parse_args()

    rules = ConfParser.parse_file(args.conf).rules

    if args.matching:
        if '-' in args.mail_file:
            sys.exit("Standard input is not supported with -m, --matching")

        for mail_file in args.mail_file:
            message_data = get_message_data(mail_file)
            for rule in rules:
                result = rule.check(message_data)
                if result:
                    print(f"{mail_file}: match on {rule.name} {result!r}")
                    break

        return

    print("Rules:")
    for rule in rules:
        print(f" - {rule.name}: {rule.rule}")

    print("Mail file check:")
    try:
        for mail_file in args.mail_file:
            if mail_file == '-':
                if sys.stdin.isatty():
                    print(" - Standard input (paste mail then ^D; ^C to quit)")
                else:
                    print(" - Standard input")
            else:
                print(f" - {mail_file}")

            show_mail_file_info(mail_file, rules)

    except KeyboardInterrupt:
        print("")


if __name__ == '__main__':
    run()
