import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request
import unittest

from fastapi import HTTPException
from storefront import server


class PlanReleaseCatalogTests(unittest.TestCase):
    def test_twelve_distinct_downloads_with_correct_terms(self):
        catalog = server.release_catalog()
        self.assertEqual(len(catalog['downloads']), 12)
        self.assertEqual(len({row['filename'] for row in catalog['downloads']}), 12)
        for row in catalog['downloads']:
            self.assertEqual(row['term_days'], 180 if row['plan'].startswith('6-Month') else 30)
            self.assertEqual(row['sha256'], server.PART_RELEASE['platforms'][row['platform']]['sha256'])
            self.assertEqual(row['parts'], server.PART_RELEASE['platforms'][row['platform']]['parts'])
        # Only the three verified engines are stored; each plan gets its own name.
        self.assertEqual(len({row['sha256'] for row in catalog['downloads']}), 3)

    def test_storage_sync_requires_owner_and_all_parts(self):
        key='test-release-catalog-owner-key'
        owner=SimpleNamespace(headers={'Authorization':'Bearer '+key})
        with patch.dict('os.environ',{'AVARA_STORE_ADMIN_KEY':key,'AVARA_INSTALLER_DOWNLOADS':'true'}):
            with self.assertRaises(HTTPException) as error:
                server.publish_release_catalog(SimpleNamespace(headers={}))
            self.assertEqual(error.exception.status_code,401)
            with patch.object(server,'plan_bundle_ready',return_value=False), patch.object(server.urllib.request,'urlopen') as opened:
                with self.assertRaises(HTTPException):server.publish_release_catalog(owner)
                opened.assert_not_called()
            request=Request('https://example.test/private-release-catalog',method='POST')
            with patch.object(server,'plan_bundle_ready',return_value=True), patch.object(server,'storage_request',return_value=request), patch.object(server.urllib.request,'urlopen',return_value=BytesIO(b'{}')):
                result=server.publish_release_catalog(owner)
                self.assertIn('12 plan downloads',result['message'])
                self.assertEqual(len(json.loads(request.data)['downloads']),12)
                self.assertEqual(request.get_header('X-upsert'),'true')

    def test_upload_uses_storage_write_route(self):
        with patch.dict('os.environ',{'SUPABASE_URL':'https://example.test','SUPABASE_SERVICE_ROLE_KEY':'sb_secret_test','AVARA_RELEASE_BUCKET':'avara-releases'}):
            upload=server.storage_request('Basic',method='POST',object_name='release-catalog.json')
            download=server.storage_request('Basic',object_name='release-catalog.json')
            self.assertEqual(upload.full_url,'https://example.test/storage/v1/object/avara-releases/release-catalog.json')
            self.assertEqual(download.full_url,'https://example.test/storage/v1/object/authenticated/avara-releases/release-catalog.json')
