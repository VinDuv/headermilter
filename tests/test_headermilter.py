from unittest.mock import Mock, patch, sentinel
import json
import pathlib
import sys
import types
import unittest

FakeMilter = types.ModuleType('FakeMilter')
FakeMilter.Base = object
FakeMilter.CONTINUE = sentinel.cont
FakeMilter.REJECT = sentinel.reject

sys.modules['Milter'] = FakeMilter

from headermilter import MessageData, ConfParser, HeaderMilter, run as run_func


DATA_PATH = pathlib.Path(__file__).parent / 'data'


class MessageDataTests(unittest.TestCase):
    def test_message_data(self):
        data = MessageData()
        data.handle_raw_header('Date', 'Sun, 22 Dec 2024 16:36:30 +0100')
        data.handle_raw_header('From', 'Sender <sender@test.abc>')
        data.handle_raw_header('To', 'single@example.org, =?UTF-8?B?VMOpc3Q=?= '
            '<test@example.org>, Some\n Extremely Long Name That Will Cause a '
            'Line Wrap <a@b.com>')
        data.handle_raw_header('Cc',
            'Other test =?UTF-8?B?PGFiw6lAZXhhbXBsZS5uZXQ+?=')
        data.handle_raw_header('Subject', 'This is a long test subject with '
            '=?UTF-8?B?c8WTbWUgc3DDqWNpYWw=?=\n ch@racters.')
        data.handle_raw_header('Message-ID',
            '<20241214213637.15a0c044@virtstable>')
        data.handle_raw_header('Reply-To',
            'M N =?UTF-8?B?w5k=?= <x@example.org>')
        data.handle_raw_header('MIME-Version', '1.0')
        data.handle_raw_header('Content-Type', 'text/plain; charset=UTF-8')
        data.handle_raw_header('Content-Transfer-Encoding', '7bit')

        self.assertEqual(data.data, {
            MessageData.Item.SUBJECT: ["This is a long test subject with sœme "
                "spécialch@racters."],
            MessageData.Item.FROM: ['sender@test.abc'],
            MessageData.Item.REPLY_TO: ['x@example.org'],
            MessageData.Item.SENDER: ['sender@test.abc', 'x@example.org'],
            MessageData.Item.TO: ['single@example.org', 'test@example.org',
                'a@b.com'],
            MessageData.Item.CC: ['abé@example.net'],
            MessageData.Item.DEST: ['single@example.org', 'test@example.org',
                'a@b.com', 'abé@example.net'],
        })

    def test_non_utf8_data(self):
        data = MessageData()
        data.handle_raw_header('From', '=?Shift_JIS?B?g3KDk4Nag5ODZw==?= '
            '<vincent@test>')
        data.handle_raw_header('To', '=?Shift_JIS?B?g2WDWINn?= '
            '<test@example.org>')
        data.handle_raw_header('Subject',
            '=?Shift_JIS?B?g26DjYFbgUWDj4Fbg4uDaIFJ?=')
        self.assertEqual(data.data, {
            MessageData.Item.SUBJECT: ["ハロー・ワールド！"],
            MessageData.Item.FROM: ['vincent@test'],
            MessageData.Item.REPLY_TO: [],
            MessageData.Item.SENDER: ['vincent@test'],
            MessageData.Item.TO: ['test@example.org'],
            MessageData.Item.CC: [],
            MessageData.Item.DEST: ['test@example.org'],
        })

    def test_bad_header_data(self):
        data = MessageData()
        data.handle_raw_header('Subject', b'\xff'.decode('utf-8',
            'surrogateescape'))
        self.assertEqual(data.data[MessageData.Item.SUBJECT], ['\udcff'])

    def test_bad_encoding(self):
        data = MessageData()
        data.handle_raw_header('To', '=?UTF-8?B?VMOpc=?= <single@example.org>')
        self.assertEqual(data.data[MessageData.Item.TO], ['single@example.org'])

        data = MessageData()
        data.handle_raw_header('To',
            '=?UTF-3?B?VMOpc3Q=?= <single@example.org>')
        self.assertEqual(data.data[MessageData.Item.TO], ['single@example.org'])

    def test_item_repr(self):
        self.assertEqual(repr(MessageData.Item.SUBJECT), 'subject')


class RulesTests(unittest.TestCase):
    def test_rules(self):
        rule, = ConfParser.parse_file(DATA_PATH / 'conf.json').rules
        self.assertEqual(rule.name, 'test')
        self.assertEqual(rule.message, "I do not like this mail")
        self.assertEqual(repr(rule.rule),
            "subject MATCHES 'Test' OR (from MATCHES 'test@example.org' AND "
                "NOT to MATCHES 'test@example.org')")

        self.assertEqual(repr(rule.rule.rules[0]), "subject MATCHES 'Test'")

        m = MessageData()
        self.assertEqual(rule.check(m), "")

        m.data[MessageData.Item.SUBJECT][0] = "not matching"
        self.assertEqual(rule.check(m), "")
        m.data[MessageData.Item.SUBJECT][0] = ""

        m.data[MessageData.Item.SUBJECT][0] = "test"
        self.assertEqual(rule.check(m), "I do not like this mail")
        m.data[MessageData.Item.SUBJECT][0] = ""

        m.data[MessageData.Item.FROM] = ['test@example.org']
        self.assertEqual(rule.check(m), "I do not like this mail")
        m.data[MessageData.Item.FROM] = []

    def test_invalid_rule_config(self):
        with self.assertRaisesRegex(SystemExit, r"Invalid character 'é' in "
            "error message"):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_invalid_chars_msg.json')

        with self.assertRaisesRegex(SystemExit, r"Unknown rule type "
            "'invalid'."):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_unk_rule_type.json')

        with self.assertRaisesRegex(SystemExit, r"Unknown match item"):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_invalid_match.json')

    def test_conf_parser(self):
        with self.assertRaisesRegex(SystemExit, r"Expecting property name"):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_invalid_json.json')

        with self.assertRaisesRegex(SystemExit, r"Expected JSON dictionary"):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_no_dict_root.json')

        with self.assertRaisesRegex(SystemExit, r"Duplicate key 'a'"):
            ConfParser.parse_file(DATA_PATH / 'bad_conf_dup_key.json')

        with open(DATA_PATH / 'sample_json.json', encoding='utf-8') as fdesc:
            sample = json.load(fdesc, cls=ConfParser)

        with self.assertRaisesRegex(ValueError, "Non-empty string expected"):
            sample.get_str('empty_string')

        with self.assertRaisesRegex(ValueError, "Expected JSON dictionary"):
            list(sample.get_dict('some_dict').dict_contents())

        with self.assertRaisesRegex(ValueError, r"some_array\[0\]\.x: Expected "
            "string"):
            sample.get_array('some_array').data[0].get_str('x')

        # Array containing non-objects
        with self.assertRaisesRegex(ValueError, "Expected JSON dictionary"):
            ConfParser.JSONArray.build(ConfParser.JSONDict(), 'test', [1])


class MilterTests(unittest.TestCase):
    @patch('headermilter.MessageData')
    def test_milter_reset(self, mock_msg_data):
        milter = HeaderMilter([])
        milter.envfrom('sender@example.org')
        mock_msg_data.assert_called_once_with()
        mock_msg_data.reset_mock()
        milter.envrcpt('receiver@example.org')
        milter.header('Subject', "Test")
        with patch('headermilter.print') as mock_print:
            milter.eoh()
            mock_print.assert_called_once_with("From sender@example.org to "
                "receiver@example.org: ACCEPT", flush=True)
        mock_msg_data.assert_not_called()
        milter.envfrom('anothersender@example.org')
        mock_msg_data.assert_called_once_with()

    @patch('headermilter.MessageData')
    def test_milter_accept(self, mock_msg_data):
        rule1 = Mock(**{'check.return_value': ""})
        milter = HeaderMilter([rule1])
        self.assertEqual(milter.envfrom('sender@example.org'), sentinel.cont)
        self.assertEqual(milter.envrcpt('receiver@example.org'), sentinel.cont)
        self.assertEqual(milter.header('Subject', "Test"), sentinel.cont)
        with patch('headermilter.print') as mock_print:
            result = milter.eoh()
            self.assertEqual(result, sentinel.cont)
            rule1.check.assert_called_once_with(mock_msg_data.return_value)
            mock_print.assert_called_once_with("From sender@example.org to "
                "receiver@example.org: ACCEPT", flush=True)

    @patch('headermilter.MessageData')
    def test_milter_reject(self, mock_msg_data):
        rule1 = Mock(**{'check.return_value': ""})
        rule2 = Mock(**{'check.return_value': "Nope"})
        milter = HeaderMilter([rule1, rule2])
        self.assertEqual(milter.envfrom('sender@example.org'), sentinel.cont)
        self.assertEqual(milter.envrcpt('receiver@example.org'), sentinel.cont)
        self.assertEqual(milter.header('Subject', "Test"), sentinel.cont)
        with patch('headermilter.print') as mock_print, patch('headermilter.'
            'HeaderMilter.setreply', create=True) as mock_setreply:
            result = milter.eoh()
            self.assertEqual(result, sentinel.reject)
            rule1.check.assert_called_once_with(mock_msg_data.return_value)
            rule2.check.assert_called_once_with(mock_msg_data.return_value)
            mock_print.assert_called_once_with("From sender@example.org to "
                "receiver@example.org: REJECT Nope", flush=True)


class MainFuncTest(unittest.TestCase):
    @patch('headermilter.Milter', None)
    def test_main_func_no_milter(self):
        cfg_path = str((DATA_PATH / 'conf.json').absolute())

        with self.assertRaisesRegex(SystemExit, "Unable to import the Milter "
            "module"):
            run_func(['-c', cfg_path])

    @patch('headermilter.Milter')
    @patch('headermilter.os')
    def test_main_func(self, mock_os, mock_milter):
        cfg_path = str((DATA_PATH / 'conf.json').absolute())
        mock_milter.factory = None

        with patch('headermilter.print') as mock_print:
            run_func(['-c', cfg_path])
            mock_print.assert_called_once_with("Running milter", flush=True)

        self.assertIsNot(mock_milter.factory, None)
        mock_milter.set_flags.assert_called_once_with(0)
        mock_os.umask.assert_called_once_with(0o555)
        mock_milter.runmilter.assert_called_once_with('headermilter',
            '/path/to/some/socket', timeout=1)
