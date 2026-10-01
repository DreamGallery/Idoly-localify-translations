import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure_runner as runner


class RunnerConfigurationTests(unittest.TestCase):
    def test_private_modes_token_preservation_and_signing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installation = {key: str(root/key) for key in
                            ('python','api_python','java_home','android_home','solis_dir','bin_dir')}
            (root/'installation.json').write_text(json.dumps(installation))
            toolkit = root/'toolkit'
            toolkit.mkdir()
            (toolkit/'config.example.ini').write_text('[Decryption settings]\n')
            config = root/'config'
            config.mkdir()
            (config/'resource-han-rounded.bundle').write_bytes(b'fixture-font')
            data = {'schema_version':1,'octo_settings':{},'firebase_settings':{},
                    'notice_account':{'schema':1,'purpose':'dedicated_notice_collector','generation':1},
                    'keystore':base64.b64encode(b'fixture-key').decode(),'decryption':{},
                    'signing':{'IDOLY_KEYSTORE_PASSWORD':'fixture','IDOLY_KEY_ALIAS':'fixture','IDOLY_KEY_PASSWORD':'fixture'}}
            def encode():return base64.b64encode(json.dumps(data).encode()).decode()
            with patch.object(runner,'FONT_SHA',hashlib.sha256(b'fixture-font').hexdigest()):
                path,_=runner.configure(root,toolkit,encode())
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertEqual(config.stat().st_mode & 0o777,0o700)
                account=config/'collector-account.json'
                account.write_text(json.dumps({'schema':1,'purpose':'dedicated_notice_collector','generation':2}))
                runner.configure(root,toolkit,encode())
                self.assertEqual(json.loads(account.read_text())['generation'],2)
                data['keystore']=base64.b64encode(b'other-key').decode()
                with self.assertRaisesRegex(ValueError,'signing key differs'):
                    runner.configure(root,toolkit,encode())

    def test_player_account_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            data={'schema_version':1,'notice_account':{'schema':1,'purpose':'player'}}
            with self.assertRaisesRegex(ValueError,'dedicated notice collector'):
                runner.configure(root,root,base64.b64encode(json.dumps(data).encode()).decode())
            self.assertFalse((root/'config').exists())


if __name__=='__main__':unittest.main()
