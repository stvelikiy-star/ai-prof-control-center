from __future__ import annotations
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ai_prof_fifo_activation as a

SHA='a'*40

class ActivationTests(unittest.TestCase):
    def good_audit(self):
        return {'ready':True,'route':'legacy','revision':SHA,'gateway_attested':True}

    def test_no_arguments_are_readonly(self):
        with mock.patch.object(a,'audit',return_value=self.good_audit()), \
             mock.patch.object(a,'activate') as activate, \
             mock.patch.object(a,'rollback') as rollback, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(a.main([]),0)
            self.assertIn('"ready": true',out.getvalue())
            activate.assert_not_called();rollback.assert_not_called()

    def test_readonly_audit_never_executes_or_transitions(self):
        with mock.patch.object(a.route,'mode',return_value='legacy'), \
             mock.patch.object(a.route,'verify_gateway_attested',return_value=False), \
             mock.patch.object(a.review,'review',return_value={'ready':True}), \
             mock.patch.object(a.baseline,'resource_gate',return_value=(True,'PASS')), \
             mock.patch.object(a.worker,'safe_codex_launch_gate',return_value=(True,'PASS')), \
             mock.patch.object(a,'services_active',return_value=True), \
             mock.patch.object(a,'revision',return_value=SHA), \
             mock.patch.object(a.route,'transition') as transition:
            result=a.audit()
            self.assertFalse(result['ready'])
            self.assertFalse(result['gateway_attested'])
            transition.assert_not_called()

    def test_all_gates_required_for_ready(self):
        with mock.patch.object(a.route,'mode',return_value='legacy'), \
             mock.patch.object(a.route,'verify_gateway_attested',return_value=True), \
             mock.patch.object(a.review,'review',return_value={'ready':True}), \
             mock.patch.object(a.baseline,'resource_gate',return_value=(True,'PASS')), \
             mock.patch.object(a.worker,'safe_codex_launch_gate',return_value=(True,'PASS')), \
             mock.patch.object(a,'services_active',return_value=True), \
             mock.patch.object(a,'revision',return_value=SHA):
            self.assertTrue(a.audit()['ready'])

    def test_invalid_confirmation_blocks_before_transition(self):
        with mock.patch.object(a,'audit') as audit, mock.patch.object(a.route,'transition') as transition:
            with self.assertRaises(a.ActivationError):a.activate(expected_sha=SHA,confirmation='wrong')
            audit.assert_not_called();transition.assert_not_called()

    def test_wrong_sha_blocks_transition(self):
        with mock.patch.object(a,'audit',return_value=self.good_audit()), \
             mock.patch.object(a.route,'transition') as transition:
            with self.assertRaises(a.ActivationError):a.activate(expected_sha='b'*40,confirmation=a.CONFIRM)
            transition.assert_not_called()

    def test_unready_gateway_blocks(self):
        with mock.patch.object(a,'audit',return_value={'ready':False,'revision':SHA}), \
             mock.patch.object(a.route,'transition') as transition:
            with self.assertRaises(a.ActivationError):a.activate(expected_sha=SHA,confirmation=a.CONFIRM)
            transition.assert_not_called()

    def test_success_freezes_then_activates_fifo(self):
        with mock.patch.object(a,'audit',return_value=self.good_audit()), \
             mock.patch.object(a.route,'transition') as transition, \
             mock.patch.object(a.route,'mode',return_value='fifo'):
            self.assertEqual(a.activate(expected_sha=SHA,confirmation=a.CONFIRM),'FIFO_OWNER_ROUTE_ACTIVE')
            self.assertEqual([c.args[0] for c in transition.call_args_list],['frozen','fifo'])

    def test_second_transition_failure_rolls_back_legacy(self):
        calls=[]
        def transition(mode):
            calls.append(mode)
            if mode=='fifo':raise a.route.RouteError('simulate failure')
        with mock.patch.object(a,'audit',return_value=self.good_audit()), \
             mock.patch.object(a.route,'transition',side_effect=transition):
            with self.assertRaises(a.route.RouteError):a.activate(expected_sha=SHA,confirmation=a.CONFIRM)
        self.assertEqual(calls,['frozen','fifo','legacy'])

    def test_rollback_failure_does_not_claim_success(self):
        def transition(mode):
            if mode!='frozen':raise a.route.RouteError('fail closed')
        with mock.patch.object(a,'audit',return_value=self.good_audit()), \
             mock.patch.object(a.route,'transition',side_effect=transition):
            with self.assertRaises(a.route.RouteError):a.activate(expected_sha=SHA,confirmation=a.CONFIRM)

    def test_fifo_rollback_uses_frozen_intermediary(self):
        with mock.patch.object(a,'revision',return_value=SHA), \
             mock.patch.object(a.route,'verify_gateway_attested',return_value=True), \
             mock.patch.object(a.route,'mode',return_value='fifo'), \
             mock.patch.object(a.route,'transition') as transition:
            self.assertEqual(a.rollback(expected_sha=SHA,confirmation=a.CONFIRM),'LEGACY_OWNER_ROUTE_RESTORED')
            self.assertEqual([c.args[0] for c in transition.call_args_list],['frozen','legacy'])

    def test_frozen_recovery_requires_attested_gateway(self):
        with mock.patch.object(a,'revision',return_value=SHA), \
             mock.patch.object(a.route,'verify_gateway_attested',return_value=False), \
             mock.patch.object(a.route,'transition') as transition:
            with self.assertRaises(a.ActivationError):a.rollback(expected_sha=SHA,confirmation=a.CONFIRM)
            transition.assert_not_called()

    def test_no_rollback_from_legacy(self):
        with mock.patch.object(a,'revision',return_value=SHA), \
             mock.patch.object(a.route,'verify_gateway_attested',return_value=True), \
             mock.patch.object(a.route,'mode',return_value='legacy'), \
             mock.patch.object(a.route,'transition') as transition:
            with self.assertRaises(a.ActivationError):a.rollback(expected_sha=SHA,confirmation=a.CONFIRM)
            transition.assert_not_called()

    def test_audit_unavailable_returns_no_go(self):
        with mock.patch.object(a.route,'mode',side_effect=a.route.RouteError('bad')):
            self.assertEqual(a.audit()['reason'],'AUDIT_UNVERIFIED')

    def test_cli_without_confirmation_cannot_mutate(self):
        with mock.patch.object(a,'activate') as activate, \
             contextlib.redirect_stderr(io.StringIO()):
            activate.side_effect=a.ActivationError('blocked')
            self.assertEqual(a.main(['--activate']),1)
            activate.assert_called_once()

if __name__=='__main__':unittest.main()
