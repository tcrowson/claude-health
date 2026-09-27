// Runs checkup.workflow.js against stub agents: no model calls, no tokens. Checks that every schema is
// satisfiable, every stub answer validates, and the assembly (dedupe, gaps, routing, statuses, yield) holds.
//   node selftest_workflow.mjs        exit code 0 when every check passes
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, 'checkup.workflow.js'), 'utf8').replace(/^export const meta/m, 'const meta')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const run = new AsyncFunction('args', 'agent', 'phase', 'log', source)

const failures = []
const check = (ok, what) => { if (!ok) failures.push(what) }

function validate(schema, value, path = '$') {
  if (schema.type === 'object') {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return [`${path}: not an object`]
    const errs = []
    for (const k of schema.required || []) {
      if (!(k in (schema.properties || {}))) errs.push(`${path}: required '${k}' is not a property (unsatisfiable)`)
      if (!(k in value)) errs.push(`${path}: missing '${k}'`)
    }
    for (const [k, v] of Object.entries(value)) if (schema.properties && schema.properties[k]) errs.push(...validate(schema.properties[k], v, `${path}.${k}`))
    return errs
  }
  if (schema.type === 'array') return Array.isArray(value) ? value.flatMap((v, i) => validate(schema.items, v, `${path}[${i}]`)) : [`${path}: not an array`]
  if (schema.type === 'string') return typeof value !== 'string' ? [`${path}: not a string`] : schema.enum && !schema.enum.includes(value) ? [`${path}: '${value}' not in enum`] : []
  if (schema.type === 'integer') return Number.isInteger(value) ? [] : [`${path}: not an integer`]
  if (schema.type === 'boolean') return typeof value === 'boolean' ? [] : [`${path}: not a boolean`]
  return []
}

const ids = (prompt) => [...prompt.matchAll(/"id":"([^"]+)"/g)].map(m => m[1])
const defect = (title, severity, file, line) => ({ title, severity, category: 'lifecycle', file, line, other_sites: [], evidence: 'x = y', failure_scenario: 'switch, then save', confidence: 'high', suggested_fix: 'reset on switch' })
const improvement = (title, lens, file, line) => ({ title, lens, file, line, other_sites: [], evidence: 'e', impact: '40 lines', effort: 'S', risk: 'low', suggested_change: 'merge' })
const reader = (read, skimmed, defects, improvements) => ({ grade: 'B', rationale: 'ok', strengths: ['clear'], defects, improvements, files_read_in_full: read, files_skimmed: skimmed })

const stubs = {
  'read:u01': () => reader(['src/a.py', 'src\\b.py'], ['src/c.py'],
    [defect('Stale cache survives switch', 'high', 'src/a.py', 10), defect('Timer not stopped', 'medium', 'src/b.py', 5), defect('Typo in log', 'low', 'src/b.py', 90)],
    [improvement('Duplicate parser', 'duplication', 'src/a.py', 30), improvement('Quadratic scan', 'performance', 'src/b.py', 40), improvement('UI imports engine internals', 'structure', 'src/a.py', 1)]),
  'read:u02': () => reader(['./src/d.py'], [],
    [defect('Stale cache survives switch again', 'high', 'src/a.py', 12)],
    [improvement('Untested save path', 'tests', 'src/d.py', 3)]),
  'read:followup-1': () => reader(['src/c.py'], [], [defect('Unchecked write result', 'medium', 'src/c.py', 7)], []),
  'extra:spec': () => reader([], [], [], [improvement('Spec says X, code does Y', 'docs', 'SPEC/x.md', 4)]),
  cartographer: () => ({ events: [{ name: 'switch', where: 'src/a.py:1', resets: 'nothing' }], holders: [{ name: 'cache', where: 'src/a.py:2', keyed_by: 'id', owner: 'A' }], cells: [{ holder: 'cache', event: 'switch', status: 'not_reset', where: 'src/a.py:2', why: 'never cleared' }], async_results: [{ producer: 'worker', lands_in: 'A.done', guard: 'none' }] }),
  'hunt:lifecycle': () => ({ defects: [defect('Late result lands on the next item', 'critical', 'src/a.py', 50)], walked: ['cache x switch'], not_walked: ['worker'], summary: 's' }),
  'hunt:failure': () => null,
  'lens:duplication': (p) => ({ verdicts: ids(p).map(id => ({ id, verdict: 'accepted', known: false, impact: '40 lines', effort: 'S', risk: 'low', strength: 'strong', reason: 'real', change: 'merge' })), improvements: [{ ...improvement('Clone group 1', 'duplication', 'src/e.py', 1), strength: 'worth_exploring' }], walked: ['clones'], not_walked: [], summary: 's' }),
  'lens:performance': (p) => ({ verdicts: ids(p).map(id => ({ id, verdict: 'rejected', known: false, reason: 'n is 10' })), improvements: [], walked: [], not_walked: ['hot path B'], summary: 's' }),
  'evaluate:1': (p) => ({ verdicts: ids(p).map((id, i) => ({ id, verdict: i ? 'accepted' : 'uncertain', known: false, reason: 'checked', effort: 'S', risk: 'low', strength: 'strong', impact: 'one test' })), summary: 's' }),
  'verify:heavy-1': (p) => ({ verdicts: ids(p).map((id, i) => ({ id, verdict: i === 1 ? 'not_real' : 'real', trigger: 'user switches', known: false, adjusted_severity: 'high', reason: 'traced', repro: 'ok' })), summary: 's' }),
  'verify:1': (p) => ({ verdicts: ids(p).map((id, i) => ({ id, verdict: i ? 'real' : 'uncertain', trigger: 't', known: i === 1, adjusted_severity: 'medium', reason: 'r' })), summary: 's' }),
}

const calls = []
async function agent(prompt, opts) {
  const key = opts.label.replace(/^(read:u0\d)-.*/, '$1')
  calls.push({ label: opts.label, model: opts.model, phase: opts.phase })
  check(opts.schema && validate(opts.schema, {}).every(e => !e.includes('unsatisfiable')), `${opts.label}: schema has a required key that is not a property`)
  const stub = stubs[key]
  if (!stub) { failures.push(`no stub for ${opts.label}`); return null }
  const out = stub(prompt)
  if (out) for (const e of validate(opts.schema, out)) failures.push(`${opts.label}: ${e}`)
  return out
}

const args = {
  dataDir: 'WORK/checkup/2026-09-27', head: 'abc123', base: null, runDate: '2026-09-27', profile: ['interactive'], cap: 1000,
  units: [
    { id: 'u01-src', name: 'src', files: ['src/a.py', 'src/b.py', 'src/c.py'], weights: [300, 300, 300], focus: 'state 3' },
    { id: 'u02-src', name: 'src', files: ['src/d.py', 'src/never.py'], weights: [200, 900], focus: 'io 2' },
  ],
  extras: [{ id: 'spec', name: 'Spec drift', brief: 'compare' }],
  cartographer: true, cartoFiles: ['src/a.py'], hunters: ['lifecycle', 'failure', 'regression'], lenses: ['duplication', 'performance'],
  followups: 1, knownPath: 'WORK/checkup/known.tsv',
}
const logs = []
const result = await run(args, agent, () => {}, (m) => logs.push(m))

const by = (id) => result.findings.find(f => f.id === id)
const status = (pred) => result.findings.filter(pred).map(f => f.status)
check(!calls.some(c => c.label === 'hunt:regression'), 'regression hunter ran without a base commit')
check(calls.filter(c => c.label.startsWith('read:followup')).length === 1, 'expected one follow-up reader')
check(result.not_covered.files.includes('src/never.py'), 'src/never.py should be reported as not covered (did not fit the follow-up)')
check(!result.not_covered.files.includes('src/b.py'), 'src\\b.py was read (backslash path) but reported as not covered')
check(result.not_covered.hunt.some(x => x.startsWith('failure:')), 'a failed hunter must show in not_covered')
const stale = result.findings.filter(f => f.kind === 'defect' && f.title.startsWith('Stale cache'))
check(stale.length === 1 && stale[0].also_reported_by.length === 1, 'the two stale-cache reports should dedupe into one')
check(result.findings.filter(f => f.kind === 'defect').every(f => f.status !== 'unverified'), 'every defect should have a verdict')
check(status(f => f.lens === 'structure').every(s => s === 'for-trajectory'), 'structure items go to the main loop')
check(status(f => f.source === 'lens:duplication' && f.id.includes('#n')).every(s => s === 'accepted') && result.findings.some(f => f.id.includes('#n')), 'lens-authored improvements are accepted')
check(status(f => f.lens === 'performance' && f.kind === 'improvement').every(s => s === 'rejected'), 'performance candidate rejected by its lens')
check(status(f => f.lens === 'tests' || f.lens === 'docs').length === 2, 'tests and docs candidates reach the evaluator')
check(result.findings.some(f => f.status === 'known'), 'a known verdict should map to status known')
check(calls.filter(c => c.label.startsWith('verify:heavy')).every(c => c.model === 'opus'), 'critical/high verifiers run on Opus')
const heavyIds = result.findings.filter(f => f.kind === 'defect' && ['critical', 'high'].includes(f.reported_severity)).length
check(heavyIds === 2, `expected 2 critical/high defects after dedupe, got ${heavyIds}`)
const readersYield = result.yield.find(y => y.lens === 'readers')
check(readersYield && readersYield.agents === 3 && readersYield.kept > 0, 'yield for readers counts 3 agents and credits kept findings')
check(Object.keys(result.counts).length > 0 && result.head === 'abc123', 'counts and head are returned')

console.log(`agents: ${calls.length} (${calls.filter(c => c.model === 'opus').length} Opus)`)
console.log(`findings: ${result.findings.length}; counts ${JSON.stringify(result.counts)}`)
console.log(`yield: ${result.yield.map(y => `${y.lens} ${y.agents}/${y.raw}/${y.kept}`).join(', ')}`)
if (failures.length) { console.log('FAIL\n  ' + failures.join('\n  ')); process.exit(1) }
console.log('PASS: workflow assembly')
