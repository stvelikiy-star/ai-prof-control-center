from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'orchestrator'))
import atomic_route_guard as route
import github_task_gateway as gateway
import github_task_gateway_service_v4 as service


class GatewayAttestationTests(unittest.TestCase):
    def test_gateway_attests_pinned_loaded_code_inside_poll(self):
        with mock.patch.object(service,'install_runtime_adapters'), \
             mock.patch.object(route,'code_digest',return_value='a'*64), \
             mock.patch.object(route,'attest_gateway') as attest, \
             mock.patch.object(gateway,'poll_once',return_value=0) as prior, \
             mock.patch.object(gateway,'main',side_effect=lambda: gateway.poll_once()):
            self.assertEqual(service.main(),0)
            attest.assert_called_once_with(start_digest='a'*64)
            prior.assert_called_once_with()

    def test_unverified_attestation_blocks_gateway_poll_before_issues(self):
        with mock.patch.object(service,'install_runtime_adapters'), \
             mock.patch.object(route,'code_digest',return_value='b'*64), \
             mock.patch.object(route,'attest_gateway',side_effect=route.RouteError('no')), \
             mock.patch.object(gateway,'poll_once',return_value=0) as prior, \
             mock.patch.object(gateway,'main',side_effect=lambda: gateway.poll_once()):
            with self.assertRaises(gateway.GatewayError):service.main()
            prior.assert_not_called()

if __name__=='__main__':unittest.main()
