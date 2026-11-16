import ast
import unittest
from unittest.mock import patch
from portable_ast import identity
class Tests(unittest.TestCase):
    def test_dump_display_does_not_affect_identity(self):
        node=ast.parse('def f(x):\n return x+1\n')
        before=identity(node)
        with patch.object(ast,'dump',side_effect=AssertionError('must not use display renderer')):self.assertEqual(identity(node),before)
    def test_locations_and_whitespace_do_not_change_identity(self):
        self.assertEqual(identity(ast.parse('def f(x):\n return x+1\n')),identity(ast.parse('\n\ndef f( x ):\n    return x + 1\n')))
    def test_executable_change_is_detected(self):
        self.assertNotEqual(identity(ast.parse('def f(x):\n return x+1\n')),identity(ast.parse('def f(x):\n return x+2\n')))
if __name__=='__main__':unittest.main()
