// Workspaces, how often to check, where reports go, the analyst.  Keys and
// passwords go in once and are never shown again.
import { useState } from 'react'
import { post } from './api'

const SEQ = { plusvibe: 'PlusVibe', instantly: 'Instantly', emailbison: 'EmailBison', smartlead: 'Smartlead', sim: 'Demo data' }
const BLANK = { name: '', sequencer: 'plusvibe', workspace: '', key: '', base_url: '' }

export default function Settings({ app, reload, redact, toggleRedact }) {
  const s = app.settings
  const n = s.notify || {}
  const [form, setForm] = useState({
    every_hours: s.every_hours, analyst: s.analyst, model: s.model, between: s.between || 'urgent',
    weekly: s.weekly || { day: null, at: '08:00' },
    alerts: s.alerts !== false, report_at: (s.report_at || []).join(', '), autostart: !!s.autostart,
    desktop: n.desktop !== false, webhook: n.webhook || '', slack_channel: n.slack_channel || '',
    telegram_token: '', slack_bot: '', slack_app: '',
  })
  const [saved, setSaved] = useState(form)
  const dirty = JSON.stringify(form) !== JSON.stringify(saved)
  const [add, setAdd] = useState(BLANK)
  const [note, setNote] = useState('')
  const set = (k, v) => setForm({ ...form, [k]: v })
  const setIn = (k, f, v) => setForm({ ...form, [k]: { ...form[k], [f]: v } })

  function save(extra) {
    return post('/api/app/settings', { ...form, ...extra }).then(r => {
      setNote(r.error ? r.error : 'Saved.')
      if (!r.error) setSaved(form)
      reload()
      return r
    })
  }

  function testWs(name) {
    setNote('Testing ' + name + '.')
    return post('/api/app/test-workspace', { fleet: name }).then(r => setNote(r.error ? 'Test failed: ' + r.error : r.ok))
  }

  function test(channel) {
    setNote('Sending a test to ' + channel + '.')
    save({}).then(() => post('/api/app/test-notify', { channel }).then(r =>
      setNote(r.error ? 'Test failed: ' + r.error : 'Test sent to ' + channel + '. Check that it arrived.')))
  }

  return (
    <>
      <section className="sec">
        <header><h2>Workspaces</h2><span className="r">Inbox Sitrep only reads.  It changes nothing unless you allow changes for a workspace and approve each one.</span></header>
        <div className="body">
          {s.fleets.map(f => (
            <div key={f.name} className="ws">
              <b>{f.name}</b><span className="dim">{SEQ[f.sequencer]}</span>
              <span className="dim small mono">{f.workspace || f.base_url || ''}</span>
              <span className="small">{f.sequencer === 'sim' ? 'no key needed' : f.has_key ? 'key saved' : <span className="p1">no key</span>}</span>
              <select value={f.writes ? 'on' : 'off'} aria-label="Changes"
                      title="Read only: Inbox Sitrep never changes this workspace.  Allowed: it can make a change after you review and approve it, with an Undo."
                      onChange={e => save({ fleets: [{ name: f.name, writes: e.target.value === 'on' }] })}>
                <option value="off">Read only</option>
                <option value="on">Changes allowed, each approved by you</option>
              </select>
              <span style={{ display: 'flex', gap: 6 }}>
                {f.sequencer !== 'sim' && <button className="line" onClick={() => testWs(f.name)} title="One small read, to see that the key works">Test</button>}
                <button className="line" onClick={() => post('/api/app/check', { fleet: f.name }).then(() => setNote('Checking ' + f.name + ' now.  Progress is in the sidebar.'))}>Check</button>
                <button className="line" onClick={() => save({ remove: [f.name] })}>Remove</button>
              </span>
            </div>
          ))}
          <div className="ws add">
            <input placeholder="Name, e.g. Main" value={add.name} onChange={e => setAdd({ ...add, name: e.target.value })} />
            <select value={add.sequencer} onChange={e => setAdd({ ...add, sequencer: e.target.value })}>
              {Object.entries(SEQ).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {add.sequencer === 'emailbison'
              ? <input placeholder="Your EmailBison address, e.g. https://send.example.com" value={add.base_url}
                       onChange={e => setAdd({ ...add, base_url: e.target.value })} />
              : <input placeholder={add.sequencer === 'plusvibe' ? 'Workspace id' : 'Workspace (optional)'} value={add.workspace}
                       onChange={e => setAdd({ ...add, workspace: e.target.value })} />}
            <input type="password" placeholder="API key" value={add.key} onChange={e => setAdd({ ...add, key: e.target.value })} />
            <button onClick={() => save({ fleets: [add] }).then(r => {
              if (r.error) return
              setAdd(BLANK)
              if (add.sequencer !== 'sim') testWs(add.name)
            })} disabled={!add.name}>Add</button>
          </div>
        </div>
      </section>

      <section className="sec">
        <header><h2>Reports</h2><span className="r">sent to Slack and Telegram (set up below)</span></header>
        <div className="body form">
          <label>Daily SITREP at</label>
          <input value={form.report_at} placeholder="e.g. 08:00, 17:00" onChange={e => set('report_at', e.target.value)} />
          <span /><p className="faint small">One message covering every workspace: each one RED, AMBER or GREEN with its worst problem, yesterday's numbers and the decisions to make. Uses this computer's clock. Sending to the US from Europe? 08:00 covers the full US day.</p>
          <label>Weekly SITREP</label>
          <div style={{ display: 'flex', gap: 8 }}>
            <select value={form.weekly.day == null ? '' : String(form.weekly.day)}
                    onChange={e => set('weekly', { ...form.weekly, day: e.target.value === '' ? null : Number(e.target.value) })}>
              <option value="">Off</option>
              {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((d, i) =>
                <option key={d} value={String(i)}>{d}</option>)}
            </select>
            <input style={{ width: 90 }} value={form.weekly.at} onChange={e => set('weekly', { ...form.weekly, at: e.target.value })} />
          </div>
          <span /><p className="faint small">The week per workspace: domains to stop, fix and delist, campaigns to scale or stop, changes made. Saturday morning covers a full US Friday.</p>
          <span /><div style={{ display: 'flex', gap: 8 }}>
            <button className="line" onClick={() => post('/api/app/send-report', { kind: 'daily' }).then(r => setNote(r.error || 'Daily SITREP sent.'))}>Send the daily SITREP now</button>
            <button className="line" onClick={() => post('/api/app/send-report', { kind: 'weekly' }).then(r => setNote(r.error || 'Weekly SITREP sent.'))}>Send the weekly now</button>
          </div>
          <label>Between reports</label>
          <select value={form.alerts ? form.between : 'off'}
                  onChange={e => setForm({ ...form, alerts: e.target.value !== 'off', between: e.target.value === 'off' ? 'urgent' : e.target.value })}>
            <option value="urgent">Urgent alerts only</option>
            <option value="new">Urgent alerts, and each new problem as it is found</option>
            <option value="off">Nothing; the reports only</option>
          </select>
          <span /><p className="faint small">Urgent: what stops mail at scale. A Microsoft tenant block, Gmail or Microsoft starting to refuse a domain, failing authentication. Bounces are read every 15 minutes; each domain alerts once a day, at most three alerts a day per workspace. Blocklists go in the reports. PlusVibe workspaces only.</p>
        </div>
      </section>

      <section className="sec">
        <header><h2>Slack</h2><span className="r">{app.slack_bot === 'connected' ? 'Connected. Reports post to the channel; mention the app to ask.'
          : app.slack_bot && app.slack_bot.startsWith('error') ? app.slack_bot : n.webhook ? 'Webhook set: reports only' : 'Not connected'}</span></header>
        <div className="body form">
          {app.slack_bot !== 'connected' && <><span /><div className="steps-help">
            <ol>
              <li><a className="button" href={app.slack_manifest_url} target="_blank" rel="noreferrer">Create the Slack app</a>
                {' '}Slack opens with the app already set up. Pick your workspace, then <b>Next</b> and <b>Create</b>.</li>
              <li><b>Install App</b> (left menu) → <b>Install to Workspace</b> → <b>Allow</b>. Copy the <b>Bot User OAuth Token</b> (xoxb-) below.</li>
              <li><b>Basic Information</b> → <b>App-Level Tokens</b> → <b>Generate Token and Scopes</b>. Name it socket, add <code>connections:write</code>, <b>Generate</b>. Copy it (xapp-) below.</li>
              <li>Name the channel for reports, save, and send a test. Ask questions with <i>@Inbox Sitrep</i> in that channel or in a direct message.</li>
            </ol>
          </div></>}
          <label>Bot token</label>
          <input type="password" value={form.slack_bot} placeholder={n.has_slack_bot ? 'saved; type to replace' : 'xoxb-...'}
                 onChange={e => set('slack_bot', e.target.value)} />
          <label>App-level token</label>
          <input type="password" value={form.slack_app} placeholder={n.has_slack_bot ? 'saved; type to replace' : 'xapp-...'}
                 onChange={e => set('slack_app', e.target.value)} />
          <label>Report channel</label>
          <input value={form.slack_channel} placeholder="#deliverability" onChange={e => set('slack_channel', e.target.value)} />
          <span /><div><button className="line" disabled={!form.slack_channel && !form.webhook} onClick={() => test('slack')}>Send a test</button></div>
          <span /><details className="faint small"><summary>Reports only, without the app: an incoming webhook</summary>
            <input style={{ marginTop: 8, width: '100%' }} value={form.webhook} placeholder="https://hooks.slack.com/services/..."
                   onChange={e => set('webhook', e.target.value)} />
            <p>Used when no report channel is set. A webhook can post but cannot answer questions.</p>
          </details>
        </div>
      </section>

      <section className="sec">
        <header><h2>Telegram</h2><span className="r">{app.telegram === 'connected' ? 'Linked: reports come here, and you can ask questions'
          : app.telegram && app.telegram.startsWith('error') ? app.telegram : n.has_telegram_key ? 'Waiting for the pairing code' : 'Off'}</span></header>
        <div className="body form">
          <label>Bot token</label>
          <input type="password" value={form.telegram_token} placeholder={n.has_telegram_key ? 'saved; type to replace' : '123456:ABC...'}
                 onChange={e => set('telegram_token', e.target.value)} />
          {n.has_telegram_key && !(n.telegram || {}).chat_id && <><label>Pairing code</label>
            <div><b className="mono" style={{ fontSize: 20 }}>{(n.telegram || {}).code}</b>
              <span className="faint small"> Send this code to your bot in Telegram. Only that chat gets reports and answers.</span></div></>}
          {!(n.telegram || {}).chat_id && <><span /><div className="faint small steps-help">
            Reports come to your Telegram, and you can ask questions there ("which domains should I pause?") while this app is open.
            <ol>
              <li>In Telegram, open <b>@BotFather</b>, send <code>/newbot</code>, and give it a name (Inbox Sitrep) and a username ending in bot.</li>
              <li>Paste the token it gives you above and save.</li>
              <li>Open your new bot in Telegram and send it the pairing code that appears here.</li>
            </ol>
          </div></>}
          <span /><div style={{ display: 'flex', gap: 8 }}>
            <button className="line" disabled={!(n.telegram || {}).chat_id} onClick={() => test('telegram')}>Send a test</button>
            {(n.telegram || {}).chat_id && <button className="line" onClick={() => save({ telegram_unlink: true })}>Link another chat</button>}
          </div>
        </div>
      </section>

      <section className="sec">
        <header><h2>This computer</h2><span className="r">checks and reports run while this computer is awake</span></header>
        <div className="body form">
          <label>Check every</label>
          <select value={form.every_hours} onChange={e => set('every_hours', Number(e.target.value))}>
            {[1, 2, 4, 6, 8, 12, 24].map(h => <option key={h} value={h}>{h} {h === 1 ? 'hour' : 'hours'}, while the app is open</option>)}
          </select>
          <label>Open at login</label>
          <select value={form.autostart ? 'on' : 'off'} onChange={e => set('autostart', e.target.value === 'on')}>
            <option value="on">Yes, so checks and reports happen without opening it</option><option value="off">No</option>
          </select>
          <label>Rules</label>
          <div className="small"><span className="dim">Thresholds for stop, fix and delist, winners and losers, the reserve
            and mailbox volume are in </span><span className="mono">{app.rules}</span><span className="dim">. Edit it in any text
            editor; the next check uses it.</span>
            {(app.rules_problems || []).map((p, i) => <div key={i} className="p2">{p}</div>)}</div>
          <label>Hide names on screen</label>
          <div><button className={'line' + (redact ? ' on' : '')} onClick={toggleRedact}>{redact ? 'Names hidden: show them' : 'Hide names'}</button>
            <span className="faint small"> Replaces every workspace and domain name with bars, for screenshots of real data. Shortcut: R.</span></div>
          <label>Desktop notifications</label>
          <select value={form.desktop ? 'on' : 'off'} onChange={e => set('desktop', e.target.value === 'on')}>
            <option value="on">On, for new problems</option><option value="off">Off</option>
          </select>
        </div>
      </section>

      <section className="sec">
        <header><h2>Analyst</h2></header>
        <div className="body form">
          <label>Analyst</label>
          <select value={form.analyst ? 'on' : 'off'} onChange={e => set('analyst', e.target.value === 'on')}>
            <option value="on">On: writes the reports and answers questions</option><option value="off">Off: automatic summaries only</option>
          </select>
          <label>Model</label>
          <select value={form.model} onChange={e => set('model', e.target.value)}>
            <option value="sonnet">Sonnet: fast, light on your limits</option>
            <option value="opus">Opus: most thorough, uses more</option>
            <option value="haiku">Haiku: fastest</option>
          </select>
          <span /><p className="faint small">{app.analyst ? 'Runs on your Claude Code login.  No API key needed.' : 'Claude Code is not installed, so reports are automatic summaries.'}</p>
        </div>
      </section>
      <div className="savebar">
        <button onClick={() => save({})} disabled={!dirty}>{dirty ? 'Save changes' : 'Saved'}</button>
        <span className={dirty ? 'p2' : 'dim'}>{dirty ? 'Unsaved changes.' : note}</span>
      </div>
    </>
  )
}
