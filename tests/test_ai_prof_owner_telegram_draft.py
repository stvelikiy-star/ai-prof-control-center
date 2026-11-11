from __future__ import annotations
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from orchestrator import ai_prof_approved_task_publisher_gate as gate
from orchestrator import submit_task as intake

BRANCH='feature/telegram-01234567-0123456789ab'
TASK='AI_PROF_CONTROL_CENTER_20261111T063000Z_ABCDEF'
PR='https://github.com/stvelikiy-star/ai-prof-control-center/pull/999'
SHA='c'*40

class OwnerTelegramDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'control'
        (self.root/'orchestrator').mkdir(parents=True)
        self.state=Path(self.tmp.name)/'runtime'
        self.approved=self.state/'queue/approved';self.approved.mkdir(parents=True)
        (self.state/'queue/completed').mkdir()
        logs=self.state/'logs/orchestrator';logs.mkdir(parents=True)
        (logs/f'{TASK}-01B-20261111T060000Z.log').write_text('STAGE_01B_CODEX_PASS\n')
        (logs/f'{TASK}-01C-20261111T060100Z.log').write_text('STAGE_01C_AUDIT_PASS\n')
        self.profile={
            'project_id':gate.PROJECT_ID,'path':gate.PROJECT_PATH,
            'base_branch':gate.BASE_BRANCH,'allowed_base_branches':[gate.BASE_BRANCH],
            'agent_context':gate.AGENT_CONTEXT,'allowed_scope':['reports/**'],
            'forbidden_scope':['.git/**','queue/**'],'allow_commits':True,
            'allow_push':False,'allow_merge':False,'allow_deployment':False,
            'require_clean_repository':True,
        }
        (self.root/'orchestrator/projects.json').write_text(json.dumps({'projects':[self.profile]}))

    def task(self, *, branch=BRANCH, action='draft-pr', source='telegram-owner'):
        fields={
          'Task-ID':TASK,'Execution-Mode':'code','Operation-Profile':'none',
          'Project-Path':gate.PROJECT_PATH,'Base-Branch':gate.BASE_BRANCH,
          'Work-Branch':branch,'Agent-Context':gate.AGENT_CONTEXT,
          'Scope-Files':'reports/evidence.md','Publication-Action':action,
          'Publication-Contract-Version':'4','Publication-Repository':gate.SOURCE_REPOSITORY,
          'Publication-Source':source,
        }
        return ''.join(f'{k}: {v}\n' for k,v in fields.items())

    def test_owner_telegram_task_is_selected_but_no_other_project_allowed(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task())
        record=gate._read_approved_task(path,self.approved)
        self.assertEqual(record['source']['kind'],'telegram_owner')
        self.assertEqual(record['work_branch'],BRANCH)
        self.assertEqual(gate._validate_task(record)[0],TASK)

    def test_wrong_branch_owner_or_contract_rejected(self):
        for changed in (self.task(branch='feature/other'),
                        self.task(source='unknown'),
                        self.task().replace('Contract-Version: 4','Contract-Version: 3')):
            path=self.approved/f'{TASK}.md';path.write_text(changed)
            with self.assertRaises(gate.MetadataError):
                gate._read_approved_task(path,self.approved)

    def test_exact_approved_task_only_publishes_after_both_audits(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task())
        with mock.patch.object(gate,'_repository_base_sha',return_value='a'*40), \
             mock.patch.object(gate,'commit_approved_change',return_value=SHA) as commit, \
             mock.patch.object(gate,'_publish_verified_owner_draft',return_value=PR) as publish:
            result=gate.run_once(self.root,self.state)
            self.assertEqual(result.decision,'PUBLISHED')
            self.assertTrue(result.published and result.complete)
            self.assertEqual(result.pull_request,PR)
            self.assertFalse(path.exists())
            completed=(self.state/'queue/completed'/f'{TASK}.md').read_text()
            self.assertIn(f'Draft-PR: {PR}',completed)
            commit.assert_called_once();publish.assert_called_once()
            self.assertEqual(gate.run_once(self.root,self.state).decision,'OWNER_ACTION_REQUIRED')

    def test_missing_audit_never_commits_or_pushes(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task())
        (self.state/'logs/orchestrator'/f'{TASK}-01C-20261111T060100Z.log').unlink()
        with mock.patch.object(gate,'commit_approved_change') as commit, \
             mock.patch.object(gate,'_publish_verified_owner_draft') as publish:
            result=gate.run_once(self.root,self.state)
            self.assertEqual(result.decision,'BLOCKED')
            commit.assert_not_called();publish.assert_not_called()
            self.assertTrue(path.exists())

    def test_out_of_scope_task_no_publication(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task().replace('reports/evidence.md','.env'))
        with mock.patch.object(gate,'commit_approved_change') as commit:
            self.assertEqual(gate.run_once(self.root,self.state).decision,'BLOCKED')
            commit.assert_not_called()

    def test_verified_draft_publisher_never_merges_or_deploys(self):
        auth=gate.CommitAuthorization(TASK,BRANCH,'a'*40,('reports/evidence.md',))
        commands=[]
        def runner(argv,*,cwd):
            commands.append(argv)
            if argv[:3]==['gh','pr','list']:
                output='[]'
            elif argv[:3]==['gh','pr','create']:
                output=PR+'\n'
            elif argv[:3]==['gh','pr','view']:
                output=json.dumps({'url':PR,'headRefOid':SHA,'isDraft':True,
                                   'baseRefName':'main','state':'OPEN'})
            else:output=''
            return subprocess.CompletedProcess(argv,0,output,'')
        with mock.patch.object(gate,'_verify_exact_commit'),mock.patch.object(gate,'_run',side_effect=runner):
            self.assertEqual(gate._publish_verified_owner_draft(Path(self.tmp.name),auth,SHA),PR)
        self.assertTrue(any(c[:3]==['gh','pr','create'] and '--draft' in c for c in commands))
        self.assertFalse(any('merge' in c or 'deploy' in c or '--force' in c for c in commands))
        self.assertTrue(any(c[:2]==['git','push'] for c in commands))

    def test_existing_draft_is_reused_exactly(self):
        auth=gate.CommitAuthorization(TASK,BRANCH,'a'*40,('reports/evidence.md',))
        commands=[]
        info={'url':PR,'headRefOid':SHA,'isDraft':True,'baseRefName':'main','state':'OPEN'}
        def runner(argv,*,cwd):
            commands.append(argv)
            return subprocess.CompletedProcess(argv,0,json.dumps([info]) if argv[:3]==['gh','pr','list'] else json.dumps(info),'')
        with mock.patch.object(gate,'_verify_exact_commit'),mock.patch.object(gate,'_run',side_effect=runner):
            self.assertEqual(gate._publish_verified_owner_draft(Path(self.tmp.name),auth,SHA),PR)
        self.assertFalse(any(c[:3]==['gh','pr','create'] for c in commands))

    def test_draft_pr_wrong_remote_sha_not_completed(self):
        auth=gate.CommitAuthorization(TASK,BRANCH,'a'*40,('reports/evidence.md',))
        info={'url':PR,'headRefOid':'d'*40,'isDraft':True,'baseRefName':'main','state':'OPEN'}
        def runner(argv,*,cwd):
            return subprocess.CompletedProcess(argv,0,json.dumps([info]) if argv[:3]==['gh','pr','list'] else json.dumps(info),'')
        with mock.patch.object(gate,'_verify_exact_commit'),mock.patch.object(gate,'_run',side_effect=runner):
            with self.assertRaises(gate.CommitBlocked):
                gate._publish_verified_owner_draft(Path(self.tmp.name),auth,SHA)

    def test_retry_after_terminal_link_reconciles_once(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task())
        expected=path.read_text().rstrip('\n')+f'\nCommit-SHA: {SHA}\nDraft-PR: {PR}\n'
        destination=self.state/'queue/completed'/f'{TASK}.md'
        destination.write_text(expected)
        gate._complete_owner_telegram_task(self.state,TASK,SHA,PR)
        self.assertFalse(path.exists())
        self.assertEqual(destination.read_text(),expected)

    def test_conflicting_completed_result_is_never_overwritten(self):
        path=self.approved/f'{TASK}.md';path.write_text(self.task())
        destination=self.state/'queue/completed'/f'{TASK}.md'
        destination.write_text('conflicting result')
        with self.assertRaises(gate.CommitBlocked):
            gate._complete_owner_telegram_task(self.state,TASK,SHA,PR)
        self.assertTrue(path.exists())
        self.assertEqual(destination.read_text(),'conflicting result')

    def test_new_task_renders_owner_draft_rights_without_merge(self):
        rendered=intake.render_task({'path':gate.PROJECT_PATH,'base_branch':gate.BASE_BRANCH,
                                     'agent_context':gate.AGENT_CONTEXT,'code_required_commands':['git','python3'],
                                     'code_required_checks':[]},TASK,'Fix','Change README',BRANCH,
                                    ['README.md'],publication_action='draft-pr')
        self.assertIn('Out-of-Scope: All files outside Scope-Files; merge, deployment',rendered)

if __name__=='__main__':unittest.main()
