"""Authenticated host smoke test. Password path supplied privately; never logged."""
import argparse
import time
import hashlib
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, Error

parser=argparse.ArgumentParser()
parser.add_argument('--url',default='http://127.0.0.1:19120')
parser.add_argument('--password-file',required=True)
parser.add_argument('--audio',help='Optional synthetic WAV to upload and transcribe')
args=parser.parse_args()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context()
    context.add_init_script("localStorage.setItem('hermes-onboarding-skipped-v1','1'); localStorage.setItem('hermes.desktop.pluginDecisions.v2',JSON.stringify({meeting_notes:true}))")
    for attempt in range(30):
        try:
            ready=context.request.get(args.url+'/api/status',timeout=2000)
            if ready.ok:
                break
        except Error:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Webapp did not become ready within 30 health probes')
    response=context.request.post(args.url+'/auth/password-login',data={
        'provider':'basic','username':'spencer','password':Path(args.password_file).read_text().strip()})
    assert response.status==200, f'Login failed: HTTP {response.status}'
    print('Authenticated with password provider')
    ping=context.request.get(args.url+'/api/plugins/meeting_notes/ping')
    print('Plugin ping:',ping.status,ping.text()[:300])
    page=context.new_page()
    page.goto(args.url,wait_until='domcontentloaded')
    page.get_by_role('button',name='Meeting Notes',exact=True).click(timeout=20000)
    print('Plugin UI loaded:',page.get_by_role('heading',name='Meeting Notes',exact=True).is_visible(),flush=True)
    if args.audio:
        title='Synthetic host test '+str(int(time.time()))
        page.get_by_role('navigation',name='Meetings',exact=True).get_by_role('button',name='New recording',exact=True).click()
        page.get_by_label('Meeting title',exact=True).fill(title)
        page.locator('input[type=file]').set_input_files(args.audio)
        page.get_by_text(re.compile(r'^Selected: ')).wait_for()
        with page.expect_response(lambda r:'/finalize' in r.url and r.request.method=='POST',timeout=45000) as finalized:
            page.get_by_role('button',name='Upload and transcribe',exact=True).click()
        response=finalized.value
        assert response.status==200, f'Finalize failed: HTTP {response.status}'
        result=response.json()
        ident=result['id']
        assert result['sha256']==hashlib.sha256(Path(args.audio).read_bytes()).hexdigest()
        page.close()
        print('Browser upload finalized with matching hash; tab closed during work',flush=True)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            meeting=context.request.get(args.url+'/api/plugins/meeting_notes/meetings/'+ident).json()
            state=meeting['job']['state']
            if state in ('completed','failed','invalid_response','interrupted_unknown'):
                break
            time.sleep(1)
        assert state=='completed', meeting['job']
        print('Background transcription completed after closing tab:',len(meeting['segments']),'segments',flush=True)
        page=context.new_page()
        page.goto(args.url,wait_until='domcontentloaded')
        page.get_by_role('button',name='Meeting Notes',exact=True).click(timeout=20000)
        page.get_by_role('navigation',name='Meetings',exact=True).get_by_role('button',name=re.compile('^'+re.escape(meeting['title']))).click()
        page.get_by_role('button',name=re.compile('^Play from ')).first.click()
        page.wait_for_function("() => document.querySelector('audio')?.readyState >= 2",timeout=15000)
        page.locator('audio').evaluate('(a)=>a.play()')
        page.wait_for_function("() => document.querySelector('audio')?.currentTime > 0.1",timeout=15000)
        print('WAV blob playback passed in authenticated Webapp',flush=True)
        panel=page.get_by_role('complementary',name='Speakers',exact=True)
        if not panel.is_visible():
            page.get_by_role('button',name=re.compile(r'^Speakers \d+$')).click()
        panel.get_by_role('button',name=re.compile('^Rename ')).first.click()
        editor=panel.get_by_role('textbox',name=re.compile('^Name for '))
        editor.fill('Demo speaker')
        editor.press('Enter')
        panel.get_by_role('button',name='Rename Demo speaker',exact=True).wait_for()
        page.get_by_role('button',name=re.compile('^Undo last change: Named Participant ')).click()
        panel.get_by_role('button',name='Rename Demo speaker',exact=True).wait_for(state='hidden')
        print('UI rename and undo passed',flush=True)
        page.screenshot(path='/tmp/hermes-test-plugin.png')
    browser.close()
