export const meta = {
  name: 'checkup',
  description: 'Whole-codebase checkup: full-read readers, event-by-state map, Opus hunters, improvement lenses, and verification or evaluation of every finding',
  whenToUse: 'Launched by the checkup skill with the workflow_args that partition.py writes into plan.json',
  phases: [
    { title: 'Read', detail: 'readers, extras and the cartographer' },
    { title: 'Hunt', detail: 'lifecycle, failure-path, regression and security hunters' },
    { title: 'Gaps', detail: 'follow-up readers for files nobody read in full' },
    { title: 'Improve', detail: 'duplication and performance lenses; evaluators for the other improvements' },
    { title: 'Verify', detail: 'every defect: Opus for critical and high, Sonnet for the rest' },
  ],
}

// ---------- args (plan.json workflow_args, focus lines edited by the main loop) ----------
// { dataDir, head, base?, runDate, profile[], cap, units: [{id, name, files, weights?, focus, model?}],
//   extras?: [{id, name, brief, model?, agentType?}], cartographer?, cartoFiles?[], hunters?[], lenses?[],
//   followups?, knownPath, readerModel?, opusBatch?, sonnetBatch? }
const A = args || {}
if (!A.dataDir || !Array.isArray(A.units) || !A.units.length) throw new Error('args need dataDir and units: use workflow_args from plan.json (see SKILL.md)')
const DIR = A.dataDir
const CLONES = `${DIR}/clones.json`
const METRICS = `${DIR}/metrics.json`
const READER_MODEL = A.readerModel || 'sonnet'
const FOLLOWUPS = A.followups ?? 2
const CAP = A.cap || 12000
const OPUS_BATCH = A.opusBatch || 10
const SONNET_BATCH = A.sonnetBatch || 40
const HEAVY = ['critical', 'high']
const KEPT = ['confirmed', 'accepted']

// ---------- schemas ----------
const SEV = ['critical', 'high', 'medium', 'low']
const DEFECT_CATEGORIES = ['correctness', 'data-integrity', 'lifecycle', 'concurrency', 'lifetime', 'invariant', 'error-handling', 'security', 'regression']
const IMPROVEMENT_LENSES = ['duplication', 'simplification', 'performance', 'structure', 'tests', 'docs']
const EFFORT = ['S', 'M', 'L']
const RISK = ['low', 'medium', 'high']
const STRENGTH = ['strong', 'worth_exploring', 'speculative']
const str = { type: 'string' }
const strs = { type: 'array', items: str }
const DEFECT = {
  type: 'object',
  properties: {
    title: str, severity: { type: 'string', enum: SEV }, category: { type: 'string', enum: DEFECT_CATEGORIES },
    file: str, line: { type: 'integer' }, other_sites: strs, evidence: str, failure_scenario: str,
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] }, suggested_fix: str,
  },
  required: ['title', 'severity', 'category', 'file', 'line', 'evidence', 'failure_scenario', 'confidence', 'suggested_fix'],
}
const IMPROVEMENT_PROPS = {
  title: str, lens: { type: 'string', enum: IMPROVEMENT_LENSES }, file: str, line: { type: 'integer' }, other_sites: strs,
  evidence: str, impact: str, effort: { type: 'string', enum: EFFORT }, risk: { type: 'string', enum: RISK },
  measurement: str, suggested_change: str,
}
const IMPROVEMENT_REQUIRED = ['title', 'lens', 'file', 'line', 'evidence', 'impact', 'effort', 'risk', 'suggested_change']
const IMPROVEMENT = { type: 'object', properties: IMPROVEMENT_PROPS, required: IMPROVEMENT_REQUIRED }
const RATED_IMPROVEMENT = {
  type: 'object',
  properties: { ...IMPROVEMENT_PROPS, strength: { type: 'string', enum: STRENGTH } },
  required: [...IMPROVEMENT_REQUIRED, 'strength'],
}
const READ = {
  type: 'object',
  properties: {
    grade: { type: 'string', enum: ['A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D', 'F'] }, rationale: str, strengths: strs,
    defects: { type: 'array', items: DEFECT }, improvements: { type: 'array', items: IMPROVEMENT },
    files_read_in_full: strs, files_skimmed: strs,
  },
  required: ['grade', 'rationale', 'strengths', 'defects', 'improvements', 'files_read_in_full', 'files_skimmed'],
}
const obj = (props, required) => ({ type: 'object', properties: props, required })
const MAP = obj({
  events: { type: 'array', items: obj({ name: str, where: str, resets: str }, ['name', 'where', 'resets']) },
  holders: { type: 'array', items: obj({ name: str, where: str, keyed_by: str, owner: str }, ['name', 'where', 'keyed_by', 'owner']) },
  cells: { type: 'array', items: obj({ holder: str, event: str, status: { type: 'string', enum: ['reset', 'not_reset', 'unclear'] }, where: str, why: str }, ['holder', 'event', 'status', 'why']) },
  async_results: { type: 'array', items: obj({ producer: str, lands_in: str, guard: str }, ['producer', 'lands_in', 'guard']) },
}, ['events', 'holders', 'cells', 'async_results'])
const HUNT = obj({ defects: { type: 'array', items: DEFECT }, walked: strs, not_walked: strs, summary: str }, ['defects', 'walked', 'not_walked', 'summary'])
const IVERDICT = obj({
  id: str, verdict: { type: 'string', enum: ['accepted', 'rejected', 'merged', 'uncertain'] }, merged_into: str,
  known: { type: 'boolean' }, known_where: str, impact: str, effort: { type: 'string', enum: EFFORT },
  risk: { type: 'string', enum: RISK }, strength: { type: 'string', enum: STRENGTH }, reason: str, change: str,
}, ['id', 'verdict', 'known', 'reason'])
const LENS = obj({ verdicts: { type: 'array', items: IVERDICT }, improvements: { type: 'array', items: RATED_IMPROVEMENT }, walked: strs, not_walked: strs, summary: str }, ['verdicts', 'improvements', 'walked', 'not_walked', 'summary'])
const IEVAL = obj({ verdicts: { type: 'array', items: IVERDICT }, summary: str }, ['verdicts', 'summary'])
const VERIFY = obj({
  verdicts: {
    type: 'array',
    items: obj({
      id: str, verdict: { type: 'string', enum: ['real', 'not_real', 'uncertain'] }, trigger: str, repro: str,
      known: { type: 'boolean' }, known_where: str, duplicate_of: str, adjusted_severity: { type: 'string', enum: SEV },
      reason: str, fix: str,
    }, ['id', 'verdict', 'trigger', 'known', 'adjusted_severity', 'reason']),
  },
  summary: str,
}, ['verdicts', 'summary'])

// ---------- prompts ----------
const BRIEF = `Read ${DIR}/BRIEF.md first and follow it exactly: the project, the run rules (read-only; how to run snippets), the lifecycle events, write paths and resources, the established facts and decisions, the KNOWN items you must not report (grep the known-items file it names for each file before reporting on it), and the rating scales.`

const IMPROVE_GUIDE = `IMPROVEMENTS: also report what would make this code simpler, faster or easier to change, each with a measurable impact:
- duplication: logic repeated here or elsewhere (list the other sites). A shared version must pass the deletion test: it gathers complexity now spread over several callers.
- simplification: dead code; pass-through functions or classes (delete them and nothing gets harder); indirection with a single implementation behind it (one adapter is a hypothetical seam, not a real one).
- performance: repeated work in loops, cost growing faster than the input, blocking I/O on a hot or UI thread, pure results worth caching or memoizing. Give the input size that matters and how to measure it.
- structure: dependencies that break BRIEF.md's layering rules; modules whose interface is nearly as complex as their implementation; one concept spread across many files.
- tests / docs: risky behavior no test reaches through the module's interface; docs or comments that say something the code no longer does.`

const RETURN_READ = `RETURN: grade (A: nothing above medium; B: a few mediums; C: notable debt or one high; D: several highs or structural problems; F: a critical) with a short rationale; strengths (short, concrete); defects, most severe first, at most 15 (merge repeats of one pattern and list the extra sites in other_sites; file repo-relative with forward slashes; evidence quotes the key lines; failure_scenario is a concrete action or call leading to a concrete wrong result); improvements, at most 10; files_read_in_full and files_skimmed, honestly: skimmed files go to another reader.`

function readerPrompt(u) {
  return `${BRIEF}

YOUR UNIT: ${u.name}
FILES: ${Array.isArray(u.files) ? u.files.join(', ') : u.files}
FOCUS: ${u.focus || 'general'}

Read every file in full, paging big files with offset/limit. Do not skim; the unit is sized so you can read all of it. While reading, keep two lists and use them:
1. STATE the unit keeps beyond one call: fields, maps and caches keyed by ids, paths or sessions; "current X" fields; timers; workers; module globals. For each, ask what happens on every lifecycle event in BRIEF.md. State that survives an event it should not is the most commonly missed bug class.
2. ASYNC RESULTS the unit receives (callbacks, signals, futures, timers, messages): what if the context changed before one lands? Is there a generation, epoch or identity check, and does it cover every event?
Also check: every write (what if it fails halfway; can it destroy the user's data); the invariants in BRIEF.md and CLAUDE.md; input validation where users or callers can create values the code cannot accept; resources that are never freed; how pure helpers are called (bugs hide in the orchestration more than in the helpers); and the general correctness of each function. Follow calls out of the unit only to confirm a bug.

${IMPROVE_GUIDE}

${RETURN_READ}`
}

function extraPrompt(x) {
  return `${BRIEF}

YOUR ASSIGNMENT: ${x.name}
${x.brief}

${IMPROVE_GUIDE}

RETURN: grade for what you assessed, rationale, strengths, defects (at most 15), improvements (at most 10), files_read_in_full, files_skimmed.`
}

function cartoPrompt() {
  const start = (A.cartoFiles || []).length ? `Start from these files, the densest in state, events and async work: ${A.cartoFiles.join(', ')}. Follow into other files as needed.` : 'Find the stateful parts of the application yourself.'
  return `${BRIEF}

YOUR ASSIGNMENT: build the event-by-state map of this application. You map; you do not judge bugs. ${start}
1. EVENTS: every lifecycle event, starting from the ones in BRIEF.md and adding what you discover (grep for functions and signals named like switch*, leaving, release*, reset*, clear*, teardown*, close*, remove*, delete*, forget*, undo, redo, paste, *finished, *failed, *cancel*, shutdown, and exception paths that abandon an operation). For each: its name, the function(s) that perform it (file:line), and what it resets or calls, in order.
2. HOLDERS: every piece of long-lived state keyed by an id, path, session or "the current X": map, set, list and cache fields; module globals; on-disk caches keyed by ids. For each: name, file:line, what it is keyed by, owner.
3. CELLS: for every holder and every event that could make its keys or meaning stale: reset (cite where), not_reset, or unclear, with why. Include every not_reset and unclear cell; list reset cells only where the reset is not obvious.
4. ASYNC RESULTS: every worker, job, task, timer or callback whose result is applied later: its producer, where it lands, and the guard it passes (generation, epoch or identity check, or "none").
Be exhaustive over the stateful code; skip pure computation and pure view code.`
}

const HUNTER_BRIEFS = {
  lifecycle: `LIFECYCLE HUNT. Walk every not_reset and unclear cell of the map below, and every async result whose guard is "none" or does not cover every event. For each, trace whether the stale state or late result produces a wrong outcome a user or caller can see or keep: the wrong item acted on, data written to the wrong place, an edit lost or applied to another item, a stale view, a crash. Also look for ids or keys that can be REUSED (database row ids after a delete, counters that restart per container) while state keyed by them survives. Where feasible, prove it with a small repro under BRIEF.md's run rules and quote the output. List each cell you walked in walked and each you did not reach in not_walked.`,
  failure: `FAILURE-PATH AND CONCURRENCY HUNT. (1) For every multi-step operation that writes (the write paths in BRIEF.md: databases, documents, caches, exports, file moves, imports, network calls with side effects), ask what happens when a step raises: is the user's data left consistent, is the old file destroyed, is the failure reported or silently recorded as success? (2) For every multi-step teardown or release sequence (switching, closing, shutdown), is it exception-safe, or does one failure skip the rest? (3) For every worker, thread, task and shared resource (BRIEF.md's exclusive resources, caches, sessions, connections): shared mutable state without a lock, objects used from two threads, a cancel that does nothing, work not stopped at shutdown or at a switch. Use the map below, when there is one, for teardown sequences and async results. List what you walked and what you did not reach.`,
  regression: `REGRESSION HUNT against the base commit BASE_SHA. Read git log BASE_SHA..HEAD, then git diff BASE_SHA...HEAD. For code that moved or was refactored, compare the old body (git show BASE_SHA:<old path>) with the new one: dropped guards, changed conditions or defaults, state that used to be reset and no longer is, a changed call order, work that used to happen only after a change and now happens always (or never), changed exception handling. Check the new fixes themselves for bugs. Report only behavior changes that the commit messages and docs do not describe as intended, citing the old and new code. Use the map below, when there is one, to find the seams worth comparing first. List the areas you compared in walked and those you did not reach.`,
  security: `SECURITY HUNT. Trace untrusted input (network requests, files the user opens, plugin or script input, environment, IPC, deserialized data) to where it is parsed, executed, or used in a path, query, shell command, template or deserializer. Check authentication and authorization on every entry point, secrets in code, config or logs, unsafe defaults, and resource exhaustion from input size. Report only reachable paths, naming the entry point. List what you walked and what you did not reach.`,
}
const HUNTERS = (A.hunters || []).filter(h => HUNTER_BRIEFS[h] && (h !== 'regression' || A.base) && (h !== 'lifecycle' || A.cartographer))

function mapText(m) {
  if (!m) return '(no event-by-state map this run: derive the events and state you need yourself)'
  const ev = m.events.map(e => `- ${e.name} @ ${e.where}: ${e.resets}`).join('\n')
  const cells = m.cells.filter(c => c.status !== 'reset').map(c => `- [${c.status}] ${c.holder} x ${c.event}${c.where ? ' @ ' + c.where : ''}: ${c.why}`).join('\n')
  const asy = m.async_results.map(r => `- ${r.producer} -> ${r.lands_in}; guard: ${r.guard}`).join('\n')
  const hold = m.holders.map(h => `- ${h.name} @ ${h.where} (keyed by ${h.keyed_by}; ${h.owner})`).join('\n')
  return `EVENTS\n${ev}\n\nHOLDERS\n${hold}\n\nCELLS NOT RESET OR UNCLEAR\n${cells}\n\nASYNC RESULTS\n${asy}`
}

function hunterPrompt(kind, m) {
  const brief = HUNTER_BRIEFS[kind].replaceAll('BASE_SHA', A.base || '')
  return `${BRIEF}\n\n${brief}\n\nRETURN: defects (at most 12, most severe first), walked, not_walked, summary (3 sentences on the health of what you walked).\n\nEVENT-BY-STATE MAP:\n${mapText(m)}`
}

const LENS_AGENTS = {
  duplication: {
    handles: ['duplication', 'simplification'],
    brief: `DUPLICATION AND SIMPLIFICATION LENS. Inputs: the candidates below (from readers who read every line), the clone groups in ${CLONES} (largest first; "renamed" copies differ only in names), and in ${METRICS}: pass_through, fan_in.single_use_public, dead_code_candidates and the longest functions. (1) Give every candidate a verdict. (2) Walk the clone groups, pass-throughs and dead-code candidates for proposals nobody made. Accept a proposal only when: a shared version passes the deletion test (it gathers complexity now spread over several callers; deleting it would push that complexity back out); any new seam has at least two real implementations; and the copies will not need to diverge (if they will, reject it and say so: duplication is cheaper than the wrong abstraction). Dead code needs a grep for dynamic and framework use first. Name the shared abstraction, and the design pattern when one fits. Quantify impact: lines removed, sites unified.`,
  },
  performance: {
    handles: ['performance'],
    brief: `PERFORMANCE AND EFFICIENCY LENS. Inputs: the candidates below, most_complex and longest in ${METRICS}, and the hot paths and measurement commands in BRIEF.md. (1) Give every candidate a verdict. (2) Look for more along the hot paths: cost growing faster than the input (name the input and its realistic size), repeated work in loops, N+1 queries or calls, blocking I/O on a hot or UI thread, needless copies or allocations in hot loops, pure results worth caching or memoizing (functools.lru_cache or the language's equivalent). Every accepted item carries a measurement plan: the command or probe, and the number that would confirm it. Without a measurement, strength is at most worth_exploring unless the cost is algorithmically certain at the stated input size. Run a timing snippet only when BRIEF.md's run rules allow it.`,
  },
}
const LENSES = (A.lenses || []).filter(l => LENS_AGENTS[l])
const MAIN_LOOP_LENSES = ['structure']
const RATE_RULES = `strength: strong (clear win, low risk), worth_exploring, or speculative. effort: S (under an hour), M (a day), L (more). risk: of the change breaking something.`

function candText(list) {
  return list.length ? list.map(c => JSON.stringify({ id: c.id, lens: c.lens, title: c.title, file: c.file, line: c.line, other_sites: c.other_sites || [], evidence: c.evidence, impact: c.impact, suggested_change: c.suggested_change })).join('\n') : '(none)'
}

function lensPrompt(l, cands) {
  return `${BRIEF}\n\n${LENS_AGENTS[l].brief}\n${RATE_RULES}\n\nRETURN: verdicts (one per candidate id: accepted, rejected, merged with merged_into naming the id kept, or uncertain; known when BRIEF.md's known items already cover it; impact, effort, risk and strength for accepted ones; reason under 80 words with file:line; change in one or two sentences), improvements (new proposals, at most 12, each fully rated with its strength), walked, not_walked, summary (3 sentences).\n\nCANDIDATES (${cands.length}):\n${candText(cands)}`
}

function evalPrompt(batch) {
  return `${BRIEF}\n\nYou are an improvement evaluator. For each candidate: read the cited code and check the claim (the duplication exists, the test really is missing, the doc really disagrees with the code, dead code has no caller including dynamic and framework use, the performance cost is real at a realistic input size); check it is not a KNOWN item; then rate it. Accept only what passes the deletion test and would not need a seam with a single implementation. ${RATE_RULES}\nRETURN: verdicts (one per id: accepted, rejected, merged with merged_into, or uncertain; known; impact, effort, risk and strength for accepted ones; reason under 60 words with file:line; change in one sentence), summary (how accurate the batch was).\n\nCANDIDATES (${batch.length}):\n${candText(batch)}`
}

function defectText(f) {
  return JSON.stringify({ id: f.id, title: f.title, severity: f.severity, category: f.category, file: f.file, line: f.line, other_sites: f.other_sites || [], evidence: f.evidence, failure_scenario: f.failure_scenario, suggested_fix: f.suggested_fix, also_reported_by: f.also || [] })
}

function verifyPrompt(batch, strong) {
  const how = strong
    ? `You are a senior verifier. For each finding: read the cited code; name the TRIGGER, the caller chain from a user action or a real call path at HEAD down to the cited line; try hard to refute it (a guard elsewhere, a misreading, documented intent, a KNOWN item); and attempt a small repro under BRIEF.md's run rules, quoting its output in repro, or say why a repro is not feasible.`
    : `You are a verifier. For each finding: read the cited code and name the TRIGGER, the caller chain from a user action or a real call path at HEAD down to the cited line. A finding is real only when that chain exists and produces the stated wrong result; a correct description of a mechanism nothing reachable triggers is not_real (or uncertain). Try to refute each one (a guard elsewhere, a misreading, documented intent, a KNOWN item).`
  return `${BRIEF}\n\n${how}\nThen set: verdict real / not_real / uncertain; known (true when BRIEF.md's known items or the project's docs already track it, naming where); duplicate_of (the id of another finding in this batch describing the same defect); adjusted_severity for this project's real users; reason under ${strong ? 120 : 60} words with file:line; fix in one line when real. One verdict per id. summary: how accurate the batch was and how hard you tried to refute.\n\nFINDINGS (${batch.length}):\n${batch.map(defectText).join('\n')}`
}

// ---------- helpers ----------
const words = (s) => new Set(String(s || '').toLowerCase().match(/[a-z]{4,}/g) || [])
function similar(a, b) {
  if (a.file !== b.file || Math.abs((a.line || 0) - (b.line || 0)) > 12) return false
  const wa = words(a.title), wb = words(b.title)
  let shared = 0
  for (const w of wa) if (wb.has(w)) shared++
  return shared / Math.max(1, Math.min(wa.size, wb.size)) >= 0.3
}
const sameDefect = (a, b) => similar(a, b)
const sameImprovement = (a, b) => a.lens === b.lens && similar(a, b)
const rank = (s) => SEV.indexOf(s)
function dedupe(list, same, order) {
  const kept = []
  for (const f of order ? [...list].sort(order) : list) {
    const twin = kept.find(k => same(k, f))
    if (twin) twin.also = [...(twin.also || []), f.id]
    else kept.push({ ...f })
  }
  return kept
}
function batches(list, size) {
  const sorted = [...list].sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : (a.line || 0) - (b.line || 0)))
  const out = []
  for (let i = 0; i < sorted.length; i += size) out.push(sorted.slice(i, i + size))
  return out
}
const tagged = (r, src, key, prefix) => (r && Array.isArray(r[key]) ? r[key].map((f, i) => ({ ...f, id: `${src}#${prefix}${i + 1}`, src })) : [])
const norm = (p) => String(p || '').replace(/\\/g, '/').replace(/^\.\//, '').toLowerCase()
function readSet(results) {
  const seen = results.flatMap(({ r }) => (r ? r.files_read_in_full : [])).map(norm)
  return (f) => { const n = norm(f); return seen.some(p => p === n || p.endsWith('/' + n)) }
}
function packFollowups(files) {
  const bins = Array.from({ length: FOLLOWUPS }, () => ({ files: [], weight: 0 }))
  const left = []
  for (const f of [...files].sort((a, b) => b.w - a.w)) {
    const w = f.w || CAP / 10
    const bin = bins.find(b => b.weight + w <= CAP)
    if (bin) { bin.files.push(f.file); bin.weight += w } else left.push(f.file)
  }
  return { units: bins.filter(b => b.files.length).map((b, i) => ({ id: `followup-${i + 1}`, name: 'Files the first readers did not read in full', files: b.files, focus: 'read in full what the first pass skimmed or missed' })), left }
}
const lensOf = (src) => (src.startsWith('read:') ? 'readers' : src.replace(/^(extra|hunt|lens):/, ''))

// ---------- Read, with hunters starting as soon as the map lands ----------
phase('Read')
log(`${A.units.length} readers (${READER_MODEL}), ${(A.extras || []).length} extras, cartographer ${A.cartographer ? 'yes' : 'no'}; hunters: ${HUNTERS.join(', ') || 'none'}; lenses: ${LENSES.join(', ') || 'none'}`)
const readerJobs = A.units.map(u => agent(readerPrompt(u), { label: `read:${u.id}`, phase: 'Read', schema: READ, model: u.model || READER_MODEL, effort: 'high' }).then(r => ({ src: `read:${u.id}`, u, r })))
const extraJobs = (A.extras || []).map(x => {
  const o = { label: `extra:${x.id}`, phase: 'Read', schema: READ, model: x.model || 'sonnet', effort: 'high' }
  if (x.agentType) o.agentType = x.agentType
  return agent(extraPrompt(x), o).then(r => ({ src: `extra:${x.id}`, x, r }))
})
const mapJob = A.cartographer ? agent(cartoPrompt(), { label: 'cartographer', phase: 'Read', schema: MAP, model: 'sonnet', effort: 'high' }) : Promise.resolve(null)
const huntJobs = mapJob.then(m => Promise.all(HUNTERS.map(h => agent(hunterPrompt(h, m), { label: `hunt:${h}`, phase: 'Hunt', schema: HUNT, model: 'opus', effort: 'high' }).then(r => ({ src: `hunt:${h}`, h, r })))))
const [readers, extras] = await Promise.all([Promise.all(readerJobs), Promise.all(extraJobs)])

// ---------- Gaps: files in scope that no reader read in full (computed, no critic) ----------
phase('Gaps')
const wasRead = readSet(readers)
const scope = A.units.flatMap(u => (u.files || []).map((file, i) => ({ file, w: (u.weights || [])[i] || 0 })))
const unread = scope.filter(f => !wasRead(f.file))
const { units: followUnits, left: notCovered } = FOLLOWUPS > 0 ? packFollowups(unread) : { units: [], left: unread.map(f => f.file) }
if (unread.length) log(`${unread.length} files not read in full: ${followUnits.length} follow-up readers; ${notCovered.length} left uncovered`)
const follow = await Promise.all(followUnits.map(u => agent(readerPrompt(u), { label: `read:${u.id}`, phase: 'Gaps', schema: READ, model: READER_MODEL, effort: 'high' }).then(r => ({ src: `read:${u.id}`, u, r }))))
const followRead = readSet(follow)
const stillUnread = followUnits.flatMap(u => u.files).filter(f => !followRead(f))

// ---------- Improve: lens agents and evaluators start now; hunters may still be running ----------
phase('Improve')
const finders = [...readers, ...extras, ...follow]
const rawImprovements = finders.flatMap(({ src, r }) => tagged(r, src, 'improvements', 'i'))
const candidates = dedupe(rawImprovements, sameImprovement)
const byLens = {}
const toEvaluate = []
const forTrajectory = []
for (const c of candidates) {
  if (MAIN_LOOP_LENSES.includes(c.lens)) { forTrajectory.push(c); continue }
  const owner = LENSES.find(l => LENS_AGENTS[l].handles.includes(c.lens))
  if (owner) (byLens[owner] = byLens[owner] || []).push(c)
  else toEvaluate.push(c)
}
log(`${rawImprovements.length} improvement candidates, ${candidates.length} after dedupe: ${LENSES.map(l => `${(byLens[l] || []).length} to ${l}`).join(', ') || 'no lens agents'}, ${toEvaluate.length} to evaluators, ${forTrajectory.length} structure items for the main loop`)
const lensJobs = Promise.all(LENSES.map(l => agent(lensPrompt(l, byLens[l] || []), { label: `lens:${l}`, phase: 'Improve', schema: LENS, model: 'opus', effort: 'high' }).then(r => ({ src: `lens:${l}`, l, r }))))
const evalJobs = Promise.all(batches(toEvaluate, SONNET_BATCH).map((b, i) => agent(evalPrompt(b), { label: `evaluate:${i + 1}`, phase: 'Improve', schema: IEVAL, model: 'sonnet', effort: 'high' })))

// ---------- Verify: every defect, after all finders are in ----------
const hunters = await huntJobs
const map = await mapJob
phase('Verify')
const rawDefects = [...finders, ...hunters].flatMap(({ src, r }) => tagged(r, src, 'defects', 'd'))
const defects = dedupe(rawDefects, sameDefect, (a, b) => rank(a.severity) - rank(b.severity))
const heavy = defects.filter(f => HEAVY.includes(f.severity))
const rest = defects.filter(f => !HEAVY.includes(f.severity))
log(`${rawDefects.length} defects, ${defects.length} after dedupe: ${heavy.length} critical/high to Opus, ${rest.length} to Sonnet`)
const verifyJobs = Promise.all([
  ...batches(heavy, OPUS_BATCH).map((b, i) => agent(verifyPrompt(b, true), { label: `verify:heavy-${i + 1}`, phase: 'Verify', schema: VERIFY, model: 'opus', effort: 'high' })),
  ...batches(rest, SONNET_BATCH).map((b, i) => agent(verifyPrompt(b, false), { label: `verify:${i + 1}`, phase: 'Verify', schema: VERIFY, model: 'sonnet', effort: 'high' })),
])
const [vResults, lensResults, evalResults] = await Promise.all([verifyJobs, lensJobs, evalJobs])

// ---------- Assemble the shared finding schema ----------
const dVerdict = new Map(vResults.filter(Boolean).flatMap(v => v.verdicts).map(v => [v.id, v]))
const findings = defects.map(f => {
  const v = dVerdict.get(f.id)
  const status = !v ? 'unverified' : v.known ? 'known' : v.duplicate_of ? 'duplicate' : v.verdict === 'real' ? 'confirmed' : v.verdict === 'not_real' ? 'refuted' : 'uncertain'
  return {
    id: f.id, kind: 'defect', lens: lensOf(f.src), source: f.src, category: f.category, title: f.title, file: f.file, line: f.line,
    other_sites: f.other_sites || [], evidence: f.evidence, failure_scenario: f.failure_scenario,
    severity: v && v.verdict === 'real' ? v.adjusted_severity : f.severity, reported_severity: f.severity, confidence: f.confidence,
    fix: (v && v.fix) || f.suggested_fix, status, verdict: v || null, also_reported_by: f.also || [],
  }
})
const iVerdict = new Map([...lensResults.filter(({ r }) => r).flatMap(({ r }) => r.verdicts), ...evalResults.filter(Boolean).flatMap(e => e.verdicts)].map(v => [v.id, v]))
const improvementOut = (c, v, status, evaluatedBy) => ({
  id: c.id, kind: 'improvement', lens: c.lens, source: c.src, title: c.title, file: c.file, line: c.line,
  other_sites: c.other_sites || [], evidence: c.evidence, impact: (v && v.impact) || c.impact, effort: (v && v.effort) || c.effort,
  risk: (v && v.risk) || c.risk, strength: (v && v.strength) || c.strength || null, measurement: c.measurement || '',
  change: (v && v.change) || c.suggested_change, status, evaluated_by: evaluatedBy, verdict: v || null, also_reported_by: c.also || [],
})
for (const c of candidates) {
  if (MAIN_LOOP_LENSES.includes(c.lens)) { findings.push(improvementOut(c, null, 'for-trajectory', 'main loop')); continue }
  const v = iVerdict.get(c.id)
  const status = !v ? 'unverified' : v.known ? 'known' : v.verdict === 'accepted' ? 'accepted' : v.verdict === 'rejected' ? 'rejected' : v.verdict === 'merged' ? 'merged' : 'uncertain'
  findings.push(improvementOut(c, v, status, v ? (LENSES.find(l => LENS_AGENTS[l].handles.includes(c.lens)) || 'evaluator') : 'none'))
}
for (const { src, l, r } of lensResults) {
  for (const n of tagged(r, src, 'improvements', 'n')) findings.push(improvementOut(n, null, 'accepted', `${l} (author)`))
}

// ---------- Counts, yield by lens, coverage ----------
const counts = findings.reduce((m, f) => { const k = `${f.kind}:${f.status}`; m[k] = (m[k] || 0) + 1; return m }, {})
const credited = (f) => [f.source, ...(f.also_reported_by || []).map(id => id.split('#')[0])]
const groups = {}
const addGroup = (lens, n) => { groups[lens] = groups[lens] || { lens, agents: 0, raw: 0, kept: 0 }; groups[lens].agents += n }
finders.forEach(({ src }) => addGroup(lensOf(src), 1))
hunters.forEach(({ src }) => addGroup(lensOf(src), 1))
lensResults.forEach(({ src }) => addGroup(lensOf(src), 1))
for (const f of [...rawDefects, ...rawImprovements]) if (groups[lensOf(f.src)]) groups[lensOf(f.src)].raw++
for (const { src, r } of lensResults) groups[lensOf(src)].raw += r && r.improvements ? r.improvements.length : 0
for (const f of findings) {
  if (!KEPT.includes(f.status)) continue
  for (const lens of new Set(credited(f).map(lensOf))) if (groups[lens]) groups[lens].kept++
}
const unverified = findings.filter(f => f.status === 'unverified').length
if (unverified) log(`${unverified} findings have no verdict (a verifier or evaluator failed): they are marked unverified`)
log(`Findings: ${JSON.stringify(counts)}`)

return {
  head: A.head || null, base: A.base || null, runDate: A.runDate || null, profile: A.profile || [],
  counts,
  yield: Object.values(groups),
  map,
  readers: [...readers, ...follow].map(({ u, r }) => ({ id: u.id, name: u.name, grade: r ? r.grade : null, rationale: r ? r.rationale : 'reader failed', strengths: r ? r.strengths : [], read_in_full: r ? r.files_read_in_full : [], skimmed: r ? r.files_skimmed : [] })),
  extras: extras.map(({ x, r }) => ({ id: x.id, name: x.name, grade: r ? r.grade : null, rationale: r ? r.rationale : 'failed', strengths: r ? r.strengths : [] })),
  hunters: hunters.map(({ h, r }) => ({ hunter: h, summary: r ? r.summary : 'failed', walked: r ? r.walked : [], not_walked: r ? r.not_walked : [] })),
  lenses: lensResults.map(({ l, r }) => ({ lens: l, summary: r ? r.summary : 'failed', walked: r ? r.walked : [], not_walked: r ? r.not_walked : [] })),
  not_covered: {
    files: [...notCovered, ...stillUnread],
    hunt: hunters.flatMap(({ h, r }) => (r ? r.not_walked : ['(hunter failed)']).map(x => `${h}: ${x}`)),
    lens: lensResults.flatMap(({ l, r }) => (r ? r.not_walked : ['(lens failed)']).map(x => `${l}: ${x}`)),
  },
  verifier_summaries: [...vResults.filter(Boolean).map(v => v.summary), ...evalResults.filter(Boolean).map(e => e.summary)],
  findings,
}
