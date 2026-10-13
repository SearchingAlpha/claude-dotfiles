// The pure half: the store format and the adapter, the diff, op naming, the
// ring buffer, the words, and the graph layout. No hooks run here.
import { describe, expect, test } from 'claude-code/testing'

import { hitTest, layoutEgo, stepFrom, textOf } from '../hooks/canvas'
import { allot, egoOf, propsFit } from '../hooks/ego'
import {
  adapterOf,
  branchOfHead,
  cdPrefixOf,
  deltaOf,
  diffGraphs,
  feedRow,
  fitStart,
  focusOf,
  gitDirOf,
  humanType,
  feedOf,
  joinPath,
  opNameOf,
  opRecordOf,
  parseGraph,
  patchOf,
  pushRing,
  queueOf,
  slugOf,
  sourceRefOf,
  storeDirOf,
  storeFlagOf,
  storeNameOf,
  totalsLine,
  totalsOf,
  touchedOf,
} from '../hooks/graphstore'
import { clean, cut, widthOf } from '../hooks/text'
import { columnsOf, crumbText, graphTextOf, windowOf } from '../hooks/views'
import type { OmniOp } from 'claude-code'
import { AFTER_ADD, AFTER_CREATE, SEED } from './demo-store'

const A = adapterOf({})
const DIR = '/work/graphs/dev.omni'
const seed = () => parseGraph(DIR, SEED, A)

const op = (over: Partial<OmniOp> = {}): OmniOp => ({
  t: 1_000,
  op: 'mutate create_issue',
  source: 'bash',
  store: 'dev.omni',
  ok: true,
  ms: 652,
  dNodes: 1,
  dEdges: 0,
  touched: [],
  focus: null,
  error: null,
  version: 'abc',
  ...over,
})

describe('reading a store', () => {
  test('the demo store: nodes, edges, labels, names, version, queue', () => {
    const graph = seed()

    expect(graph.nodes.size).toBe(31)
    expect(graph.edges.length).toBe(41)
    expect(graph.labels).toEqual(['Actor', 'Commit', 'Epic', 'Issue', 'Label', 'RFC', 'Release'])
    expect(graph.nodes.get('epc-offline-sync')?.name).toBe('Offline editing & sync')
    expect(graph.byLabel.get('Epic')).toEqual(['epc-tenant-isolation', 'epc-offline-sync', 'epc-sync-observability', 'epc-billing-v2'])
    expect(graph.version).toBe(JSON.parse(SEED.manifest).version)
    expect(storeNameOf(graph, A)).toBe('dev.omni')
    expect(queueOf(graph, A)).toEqual(['iss-bill-proration', 'rfc-offline'])
    expect(graph.bad).toBe(0)
  })

  test('another producer\'s build reads through the adapter options alone', () => {
    const a = adapterOf({ versionKey: 'build_id', nodeKeys: 'uid kind .', edgeKeys: 'src rel dst', nameKeys: 'title', statusKey: 'state' })
    const graph = parseGraph(
      '/b',
      {
        manifest: '{"build_id":"b7f1","nodes":2}',
        nodes: '{"uid":"n1","kind":"Thing","title":"First","state":"proposed"}\n{"uid":"n2","kind":"Thing"}\nnot json\n',
        edges: '{"src":"n1","rel":"USES","dst":"n2"}\n{"src":"n1","rel":"USES","dst":"n2"}\n',
      },
      a,
    )

    expect(graph.version).toBe('b7f1')
    expect(graph.nodes.get('n1')).toMatchObject({ label: 'Thing', name: 'First', status: 'proposed' })
    expect(graph.nodes.get('n2')?.name).toBe('n2')
    expect(graph.edges).toHaveLength(1)
    expect(graph.bad).toBe(1)
    expect(humanType('USES')).toBe('uses')
  })

  test('control characters and a byte-order mark: names, labels, types and the store name come out drawable', () => {
    const graph = parseGraph(
      DIR,
      {
        manifest: `\ufeff${JSON.stringify({ version: 'v\u001b1', name: 'evil\u001b[2Jstore' })}`,
        nodes: [
          `\ufeff${JSON.stringify({ id: 'n1', label: 'Ru\u0007le', props: { name: 'page one\fpage two\r\nline three\ttab', status: 'pro\u009bposed' } })}`,
          JSON.stringify({ id: 'n\u001b2', label: 'Rule' }),
        ].join('\n'),
        edges: `${JSON.stringify({ from: 'n1', type: 'lin\u001bks', to: 'n\u001b2' })}\n`,
      },
      A,
    )
    const hasControl = (text: string) => /[\u0000-\u001f\u007f-\u009f]/.test(text)

    expect(graph.bad).toBe(0)
    expect(graph.nodes.size).toBe(2)
    expect(graph.nodes.get('n1')).toMatchObject({ label: 'Ru le', name: 'page one page two line three tab', status: 'pro posed' })
    expect(graph.nodes.get('n\u001b2')?.name).toBe('n 2')
    expect(graph.edges[0]?.type).toBe('lin ks')
    expect(graph.out.get('n1')?.[0]?.to).toBe('n\u001b2')
    expect([storeNameOf(graph, A), graph.version]).toEqual(['evil [2Jstore', 'v 1'])
    expect([...graph.nodes.values()].some(node => hasControl(node.name + node.label + (node.status ?? '')))).toBe(false)
    expect(clean('a\u0000\u0001b')).toBe('a b')
  })

  test('a missing store reads empty; a store without a manifest gets a content hash', () => {
    expect(parseGraph(DIR, { manifest: null, nodes: null, edges: null }, A)).toMatchObject({ isPresent: false, version: null })
    const bare = parseGraph(DIR, { manifest: null, nodes: SEED.nodes, edges: SEED.edges }, A)
    expect(bare.isPresent).toBe(true)
    expect(bare.version).toMatch(/^[0-9a-f]{8}$/)
  })

  test('a source path:line, the branch and a worktree gitdir', () => {
    const graph = parseGraph('/b', { manifest: null, nodes: '{"id":"r1","label":"Rule","props":{"source":["docs/x.md:19"]}}\n', edges: '' }, A)

    expect(sourceRefOf(graph.nodes.get('r1')!, A)).toEqual({ path: 'docs/x.md', line: 19 })
    expect(branchOfHead('ref: refs/heads/cc-operating-kit\n')).toBe('cc-operating-kit')
    expect(branchOfHead('9cda71d0a1b2c3d4e5f6\n')).toBe('9cda71d')
    expect(gitDirOf('gitdir: /repo/.git/worktrees/x\n')).toBe('/repo/.git/worktrees/x')
  })
})

describe('the store diff', () => {
  test('create_issue adds one node and focuses it', () => {
    const before = seed()
    const after = parseGraph(DIR, AFTER_CREATE, A)
    const diff = diffGraphs(before, after)

    expect(diff.addedNodes).toEqual(['iss-offline-conflict-toast'])
    expect([diff.removedNodes, diff.addedEdges, diff.removedEdges]).toEqual([[], [], []])
    expect(focusOf(diff, after)).toEqual({ center: 'iss-offline-conflict-toast', selected: 'iss-offline-conflict-toast' })
  })

  test('add_issue_to_epic adds one edge: drawn around the epic, the issue highlighted', () => {
    const before = parseGraph(DIR, AFTER_CREATE, A)
    const after = parseGraph(DIR, AFTER_ADD, A)
    const diff = diffGraphs(before, after)

    expect(diff.addedEdges.map(edge => [edge.from, edge.type, edge.to])).toEqual([['epc-offline-sync', 'contains', 'iss-offline-conflict-toast']])
    expect(touchedOf(diff)).toEqual(['epc-offline-sync', 'iss-offline-conflict-toast'])
    expect(focusOf(diff, after)).toEqual({ center: 'epc-offline-sync', selected: 'iss-offline-conflict-toast' })
  })

  test('a rebuild that changes more than five records does not move the view', () => {
    const nodes = SEED.nodes.replace(/"status": "open"\}\}/g, '"status": "triaged"}}')
    const after = parseGraph(DIR, { ...SEED, nodes }, A)
    const diff = diffGraphs(seed(), after)

    expect(diff.changedNodes.length).toBeGreaterThan(5)
    expect(focusOf(diff, after)).toBeNull()
  })

  test('a changed record is a changed node; a removed one is removed', () => {
    const before = seed()
    const nodes = SEED.nodes
      .replace('"status": "open"}}', '"status": "closed"}}')
      .split('\n')
      .filter(line => !line.includes('"id": "act-jun"'))
      .join('\n')
    const diff = diffGraphs(before, parseGraph(DIR, { ...SEED, nodes }, A))

    expect(diff.changedNodes).toHaveLength(1)
    expect(diff.removedNodes).toEqual(['act-jun'])
  })
})

describe('naming ops', () => {
  const re = A.cmdRegex

  test('the default pattern names the demo CLI\'s mutations, queries and init', () => {
    const cmd =
      'omnigraph mutate add_issue_to_epic --query queries/queries.gq --store graphs/dev.omni --params \'{"epic":"epc-offline-sync","issue":"iss-offline-conflict-toast"}\''

    expect(opNameOf(cmd, re)).toBe('mutate add_issue_to_epic')
    expect(opNameOf('python3 mods/omnigraph/demo/omni.py query blocked', re)).toBe('query blocked')
    expect(opNameOf('cd demo && omnigraph init', re)).toBe('init')
    expect(opNameOf('ls mods/omnigraph', re)).toBeNull()
    expect(opNameOf('omnigraph schema', re)).toBeNull()
    expect(opNameOf('git status', re)).toBeNull()
  })

  test('the CLI must be the command: a mention in an echo, a commit message, a path or a grep is not an op', () => {
    const ops: [string, string][] = [
      ['omnigraph query blocked | wc -l', 'query blocked'],
      ['OMNIGRAPH_STORE=x omnigraph mutate close_issue --params x', 'mutate close_issue'],
      ['env A=1 ./demo/omnigraph init', 'init'],
      ['(cd graphs && omnigraph query blocked --store dev.omni)', 'query blocked'],
      ['ls graphs; omnigraph query blocked', 'query blocked'],
      ['/usr/bin/python3 demo/omni.py mutate tag_issue --params {}', 'mutate tag_issue'],
      // Loops, conditionals, groups, wrappers and their flags, leading spaces, python flags with a value.
      ['for i in a b; do omnigraph mutate close_issue --params "{\\"issue\\":\\"$i\\"}"; done', 'mutate close_issue'],
      ['if [ -d graphs ]; then omnigraph init; fi', 'init'],
      ["{ omnigraph mutate create_issue --params '{}'; }", 'mutate create_issue'],
      ['! omnigraph query blocked', 'query blocked'],
      ['timeout 30 omnigraph query blocked', 'query blocked'],
      ['sudo -u me omnigraph init', 'init'],
      ['nice -n 5 omnigraph query blocked', 'query blocked'],
      ['command omnigraph init', 'init'],
      ['xargs -n1 omnigraph mutate close_issue --params < ids.txt', 'mutate close_issue'],
      ["bash -c 'omnigraph mutate create_issue --params \"{}\"'", 'mutate create_issue'],
      ['  omnigraph init', 'init'],
      ['python3 -X dev demo/omni.py query blocked', 'query blocked'],
      ['python3 -W ignore demo/omni.py query blocked', 'query blocked'],
      ['omnigraph  mutate   create_issue --params x', 'mutate create_issue'],
    ]
    const notOps = [
      'echo omnigraph init',
      'echo next step: omnigraph init',
      'git commit -m "docs: omnigraph init seeds the store"',
      'git commit -m "omnigraph: explain how omnigraph init works"',
      'grep -rn "omnigraph mutate create_issue" docs/',
      'grep -rn omnigraph mutate docs/',
      'cat mods/omnigraph/demo/omni.py | head',
      'python3 mods/omnigraph/demo/omni.py --help',
      'python3 -m unittest discover -s mods/omnigraph/demo',
      'man omnigraph',
      'bash -c "echo omnigraph init"',
      'timeout 5 grep omnigraph init',
      'which omnigraph && omnigraph status',
    ]

    for (const [command, want] of ops) {
      expect([command, opNameOf(command, re)]).toEqual([command, want])
    }
    for (const command of notOps) {
      expect([command, opNameOf(command, re)]).toEqual([command, null])
    }
  })

  test('a long command costs one pass: 120 KB of separators, a heredoc or a query string', () => {
    const shapes = {
      semicolons: ';a'.repeat(60_000),
      parens: '(x'.repeat(60_000),
      pipes: '|/'.repeat(60_000),
      css: `cat > app.min.css <<'EOF'\n${'.c0{color:red;margin:0}'.repeat(5_000)}\nEOF`,
      query: `curl 'https://x.test/?${'&k=v'.repeat(30_000)}'`,
      blankLines: '\n '.repeat(60_000),
    }
    const times: Record<string, boolean> = {}

    for (const [shape, command] of Object.entries(shapes)) {
      const t = Date.now()
      opNameOf(command, re)
      times[shape] = Date.now() - t < 50
    }
    expect(times).toEqual({ semicolons: true, parens: true, pipes: true, css: true, query: true, blankLines: true })
  })

  test('a pattern that backtracks reads a bounded slice: the head, and the last lines past a long heredoc', () => {
    // An earlier default, whose `\S*` runs rescanned the rest of the command after every separator.
    const old = new RegExp(
      String.raw`(?:^|[\n;&|(]\s*)(?:(?:env|time|nohup|exec)\s+)?(?:[A-Za-z_]\w*=\S*\s+)*(?:(?:\S*/)?python3?(?:\.\d+)?\s+(?:-\w+\s+)*)?(?:\S*/)?(?:omnigraph|omni\.py)\s+((?:mutate|query)\s+[\w.-]+|init)\b`,
    )
    // 120 KB, and the worst for this pattern within both slices: 16 KB runs of `;a` with no white space.
    const run = ';a'.repeat(8_000)
    const heredoc = `cat > notes.css <<'EOF'\n${run} x\n${';a'.repeat(44_000)}\n${run}\nEOF\n`
    const t = Date.now()

    expect(opNameOf(`${heredoc}omnigraph query blocked`, old)).toBe('query blocked')
    expect(opNameOf(`omnigraph init; ${heredoc}`, old)).toBe('init')
    expect(opNameOf(heredoc, old)).toBeNull()
    // Unbounded, each of these took 12-13 s; bounded, about 0.25 s a slice.
    expect(Date.now() - t).toBeLessThan(3000)
    // The head is cut back to a space: a cut inside `blocked` names no `query blo`.
    const cutInside = `${'x '.repeat(8_182)}omnigraph query blocked ${'y'.repeat(20_000)}`
    expect(cutInside.slice(16_364, 16_384)).toBe('omnigraph query bloc')
    expect(opNameOf(cutInside, re)).toBeNull()
  })

  test('a configured pattern: the first group that matched names the op', () => {
    const a = adapterOf({ cmdRegex: String.raw`scripts/graph/(build|check)\.py|deploy/(load)\.py` })

    expect(opNameOf('python3 scripts/graph/build.py --all', a.cmdRegex)).toBe('build')
    expect(opNameOf('python3 deploy/load.py beta', a.cmdRegex)).toBe('load')
    expect(adapterOf({ cmdRegex: '(' })).toMatchObject({ cmdRegex: null, problem: expect.stringContaining('cmdRegex') })
    expect(adapterOf({ cmdRegex: '' }).cmdRegex).toBeNull()
  })

  test('where the store is: the flag under the cwd and a leading cd, else the option under the root', () => {
    expect(storeFlagOf('omnigraph mutate x --store graphs/other.omni --params {}', '--store')).toBe('graphs/other.omni')
    expect(storeFlagOf("omnigraph query x --store='my store'", '--store')).toBe('my store')
    expect(storeFlagOf('omnigraph query x', '--store')).toBeNull()
    expect(cdPrefixOf('cd "sub dir" && omnigraph init')).toBe('sub dir')
    expect(joinPath('/work', '../x/./y')).toBe('/x/y')
    expect(joinPath('/work', '~/g', '/home/t')).toBe('/home/t/g')
    expect(storeDirOf('/work', '/work', 'omnigraph init', A, null)).toBe('/work/graphs/dev.omni')
    expect(storeDirOf('/work', '/work/sub', 'cd .. && omnigraph init --store g/s.omni', A, null)).toBe('/work/g/s.omni')
    expect(storeDirOf('/work', '/work', null, adapterOf({ store: '/abs/build' }), null)).toBe('/abs/build')
    expect(storeDirOf('/work', '/work', '(cd graphs && omnigraph query x --store dev.omni)', A, null)).toBe('/work/graphs/dev.omni')
  })

  test('a store only the shell can spell is not guessed', () => {
    const commands = [
      'omnigraph query x --store "$OMNI_STORE"',
      'omnigraph query x --store `pwd`/g',
      'omnigraph query x --store g/*.omni',
      'omnigraph query x --store ~bob/g',
      'cd "$D" && omnigraph query x --store g',
    ]

    for (const command of commands) {
      expect([command, storeDirOf('/work', '/work', command, A, '/home/t')]).toEqual([command, null])
    }
    expect(storeDirOf('/work', '/work', 'omnigraph query x --store ~/g', A, '/home/t')).toBe('/home/t/g')
  })

  test('the ops log: the newest new record that started during the call and that the command names', () => {
    const line = (t: number, op: string, extra: Record<string, unknown> = {}) => JSON.stringify({ t, kind: 'mutate', op, ok: true, ms: 5, ...extra })
    const lines = [line(1000, 'create_issue'), line(2000, 'link_blocks', { ok: false, ms: 652, error: 'no node' }), 'garbage']
    const cmd = (op: string) => `omnigraph mutate ${op} --params '{}'`

    expect(opRecordOf(lines, 1900, 2100, cmd('link_blocks'))).toEqual({ op: 'mutate link_blocks', ok: false, ms: 652, error: 'no node', index: 1 })
    // A record from before the call, or one of another op, never stands in.
    expect(opRecordOf(lines, 5000, 5100, cmd('link_blocks'))).toBeNull()
    expect(opRecordOf(lines, 1900, 2100, cmd('tag_issue'))).toBeNull()
    expect(opRecordOf(lines, 900, 2100, cmd('create_issue'))?.index).toBe(0)
    // An MCP call names no command: the time decides.
    expect(opRecordOf(lines, 1900, 2100, null)?.op).toBe('mutate link_blocks')
    // A long error and a control character are cut and cleaned.
    const noisy = opRecordOf([line(2000, 'x', { ok: false, error: `bad\u001b[2J${'E'.repeat(5000)}` })], 2000, 2000, 'omnigraph mutate x')
    expect(noisy?.error?.length).toBe(200)
    expect(noisy?.error).toMatch(/^bad \[2JE/)
  })
})

describe('the feed', () => {
  test('the ring keeps the last 50', () => {
    let feed: number[] = []
    for (let i = 0; i < 57; i++) {
      feed = pushRing(feed, i)
    }

    expect(feed).toHaveLength(50)
    expect(feed[0]).toBe(7)
    expect(feed[49]).toBe(56)
  })

  test('totals count this session\'s ops; the header and a row read as in the pane', () => {
    const mine = { session: 'mine' }
    const feed = [
      op({ t: 1, ...mine }),
      op({ t: 5, dNodes: 2, dEdges: 3, ...mine }),
      op({ t: 6, ok: false, dNodes: 0, dEdges: 0, ...mine }),
      op({ t: 7, dNodes: null, dEdges: null, ...mine }),
      op({ t: 8, ok: false, session: 'other', dNodes: 7 }),
      op({ t: 9, ok: false, dNodes: 5 }),
    ]
    const totals = totalsOf(feed, 5, 'mine')

    expect(totals).toEqual({ ops: 3, dNodes: 2, dEdges: 3, errors: 1 })
    expect(totalsOf(feed, 5).ops).toBe(5)
    expect(totalsLine(totals)).toBe('3 ops · +2n +3e · 1 ×')
    expect(deltaOf(-2, 'e')).toBe('-2e')
    const row = feedRow(op({ op: 'mutate add_issue_to_epic', dNodes: 0, dEdges: 1, ms: 668 }), 18)
    expect([row.op, row.delta, row.ms.trim(), row.mark]).toEqual(['mutate add_issue_…', '+0n +1e', '668ms', '✓'])
    expect(row.time).toMatch(/^\d\d:\d\d:\d\d$/)
    const closed = feedRow(op({ op: 'mutate close_issue', dNodes: 0, dEdges: 0, changed: 1 }), 18)
    expect(closed.delta).toBe('+0n +0e ~1n')
    const bg = feedRow(op({ op: 'compile.py', dNodes: null, dEdges: null, isBackground: true }), 18)
    expect([bg.delta, bg.ms.trim(), bg.mark]).toEqual(['·n ·e', 'bg', '…'])
  })

  test('a stored op is made safe to draw: control characters, long text, odd fields', () => {
    const odd = { t: 1, op: 'mutate\u001b[2J x', ok: false, ms: 'slow', dNodes: 1, dEdges: 'x', touched: 'abc', focus: { center: 1 }, error: 'E'.repeat(9000) }
    const [stored, ...rest] = feedOf([odd, 'junk'])

    expect(rest).toEqual([])
    expect(stored).toMatchObject({ op: 'mutate [2J x', ms: 0, dNodes: 1, dEdges: null, touched: [], focus: null })
    expect(stored?.error?.length).toBe(200)
  })

  test('a patch is the id, the accepted status and the owner', () => {
    const node = seed().nodes.get('iss-bill-proration')!
    const patch = patchOf(node, adapterOf({ owner: 'tester', acceptStatus: 'approved' }), 'dev.omni', 'v1', '2026-10-09T10:00:00Z')

    expect(patch.split('\n').slice(2)).toEqual(['- id: "iss-bill-proration"', '  status: "approved"', '  owner: "tester"', ''])
    expect(patchOf(node, A, 'dev.omni', null, 'now')).not.toContain('owner')
  })

  test('a patch holds one record whatever the store says: a line break in a label, a name or the version adds none', () => {
    const graph = parseGraph(
      DIR,
      {
        manifest: JSON.stringify({ version: 'v1\n- id: "iss-third"\n  status: "accepted"', name: 'dev\n- id: "iss-fourth"' }),
        nodes: `${JSON.stringify({ id: 'iss-a', label: 'Issue)\n- id: "iss-other"\n  status: "accepted"\n# (', props: { name: 'A\n- id: "x"', status: 'proposed\n- id: "y"' } })}\n`,
        edges: '',
      },
      A,
    )
    const node = graph.nodes.get('iss-a')!
    const patch = patchOf(node, adapterOf({ statusKey: 'review status' }), storeNameOf(graph, A), graph.version, 'now')

    expect(patch.split('\n').filter(line => /^\s*-\s/.test(line))).toEqual(['- id: "iss-a"'])
    expect(patch.split('\n')).toHaveLength(5)
    expect(patch).toContain('\n  "review status": "accepted"\n')
  })

  test('a patch holds no Unicode line break either: U+2028 and U+2029 in a name, a label or the store, NEL and U+FFFE in the id', () => {
    const LS = '\u2028'
    const PS = '\u2029'
    const name = `x${LS}- id: 'iss-victim'${LS}  status: accepted${LS}#`
    const graph = parseGraph(
      DIR,
      {
        manifest: JSON.stringify({ version: `v1${PS}- id: "v"`, name: `st${LS}- id: "w"` }),
        nodes: `${JSON.stringify({ id: `r1\u0085x\ufffe${LS}`, label: `Rule${PS}- id: "victim2"`, props: { name, status: 'proposed' } })}\n`,
        edges: '',
      },
      A,
    )
    const node = [...graph.nodes.values()][0]!
    const patch = patchOf(node, adapterOf({ owner: `m${PS}e` }), storeNameOf(graph, A), graph.version, 'now')

    expect(/[\u0085\u2028\u2029\ufffe\uffff]/.test(patch)).toBe(false)
    expect(patch.split('\n').filter(line => /^\s*-\s/.test(line))).toEqual(['- id: "r1\\u0085x\\ufffe\\u2028"'])
    expect(patch.split('\n')).toHaveLength(6)
    expect(patch).toContain('\n  owner: "m\\u2029e"\n')
    // What a drawing shows is cleaned the same way.
    expect([node.name, node.label]).toEqual(["x - id: 'iss-victim'   status: accepted #", 'Rule - id: "victim2"'])
  })

  test('patch file names: safe ids kept, changed ones hashed, never . or ..', () => {
    expect(slugOf('iss-bill-proration')).toBe('iss-bill-proration')
    expect(slugOf('dev.omni', 'store')).toBe('dev.omni')
    expect(slugOf('..', 'store')).toMatch(/^store-[0-9a-f]{8}$/)
    expect(slugOf('.hidden')).toMatch(/^hidden-[0-9a-f]{8}$/)
    expect(new Set([slugOf('a:b'), slugOf('a/b'), slugOf('a-b')]).size).toBe(3)
    expect(slugOf(`r:${'x'.repeat(200)}1`)).not.toBe(slugOf(`r:${'x'.repeat(200)}2`))
  })
})

describe('the graph drawing', () => {
  const ego = (selected: string, rows = 16) => egoOf(parseGraph(DIR, AFTER_ADD, A), 'epc-offline-sync', selected, ['iss-offline-conflict-toast'], 72, rows, true)

  test('one node and its neighbours, grouped by edge type, the selection marked', () => {
    const props = ego('iss-offline-conflict-toast')
    const titles = props.groups.map(group => group.title)

    expect(titles).toEqual(expect.arrayContaining(['contains ×6', 'tagged ×2', 'owned by', 'targets', '← specifies']))
    expect(props.nodes.find(node => node.side === 'center')?.name).toBe('Offline editing & sync')
    expect(props.nodes.find(node => node.id === 'iss-offline-conflict-toast')).toMatchObject({ isSelected: true, isTouched: true })
    expect(props.legend.map(entry => entry.label)).toEqual(expect.arrayContaining(['Epic', 'Issue', 'Actor', 'Label', 'Release', 'RFC']))

    const lines = textOf(layoutEgo(props, 72, 16))
    expect(lines).toHaveLength(16)
    expect(lines.join('\n')).toContain('◉')
    expect(lines.join('\n')).toContain('Offline editing & sync')
    expect(lines.join('\n')).toContain('contains ×6')
    expect(lines.join('\n')).toContain('Conflict toast hides')
    expect(lines.join('\n')).toContain('Nadia Okonkwo')
    expect(lines[15]).toMatch(/^● \w+/)
  })

  test('a group too big for the rows ends in +N more, the selection kept', () => {
    const props = ego('iss-offline-retry', 6)
    const more = props.nodes.find(node => node.more > 0)

    expect(more).toBeDefined()
    expect(props.nodes.some(node => node.id === 'iss-offline-retry')).toBe(true)
    expect(props.nodes.filter(node => node.side === 'right').length).toBeLessThanOrEqual(5)
  })

  test('arrow keys move to the nearest node that way; a click picks the node under it', () => {
    const props = ego('epc-offline-sync')
    const layout = layoutEgo(props, 72, 16)
    const right = stepFrom(layout, 'epc-offline-sync', 'right')
    const left = stepFrom(layout, 'epc-offline-sync', 'left')

    expect(props.nodes.find(node => node.id === right)?.side).toBe('right')
    expect(props.nodes.find(node => node.id === left)?.side).toBe('left')
    expect(stepFrom(layout, right!, 'left')).toBe('epc-offline-sync')
    const spot = layout.spots.find(s => s.id === right)!
    expect(hitTest(layout, spot.x0 + 1, spot.y)?.id).toBe(right)
    expect(hitTest(layout, 0, 15)).toBeNull()
  })

  test('wide characters take two cells: rows keep their width, names are cut by cells', () => {
    const names: [string, string][] = [['hub', 'Hub'], ['a', '漢字の名前がとても長いノード'], ['b', '🚀🚀🚀 rocket'], ['c', 'cafe\u0301 au lait']]
    const wide = parseGraph(
      DIR,
      {
        manifest: '{"version":"v"}',
        nodes: names.map(([id, name]) => JSON.stringify({ id, label: 'Issue', props: { name } })).join('\n'),
        edges: ['a', 'b', 'c'].map(to => JSON.stringify({ from: 'hub', type: 'contains', to })).join('\n'),
      },
      A,
    )
    const layout = layoutEgo(egoOf(wide, 'hub', 'hub', [], 60, 12, true), 60, 12)

    for (const line of textOf(layout)) {
      expect(widthOf(line)).toBeLessThanOrEqual(60)
    }
    expect(textOf(layout).join('\n')).toContain('漢字')
    expect(widthOf('漢字')).toBe(4)
    expect(widthOf('🚀 x')).toBe(4)
    expect(widthOf('cafe\u0301')).toBe(4)
    expect(cut('漢字の名前', 5)).toBe('漢字…')
    const spot = layout.spots.find(s => s.id === 'a')!
    expect(hitTest(layout, spot.x1, spot.y)?.id).toBe('a')
  })

  test('the text fallback and an empty store', () => {
    expect(graphTextOf(ego('epc-offline-sync'), 80, 10)[0]).toBe('◉ Offline editing & sync')
    const empty = egoOf(parseGraph(DIR, { manifest: null, nodes: null, edges: null }, A), null, null, [], 60, 10, true)
    expect(empty.empty).toContain('no store at')
    expect(textOf(layoutEgo(empty, 60, 10))[0]).toContain('no store at')
  })

  test('the client props stay far under the engine\'s 100,000 characters: a 120 KB name, sixty 2 KB names', () => {
    const store = (nodes: object[], edges: object[]) =>
      parseGraph(DIR, { manifest: '{}', nodes: nodes.map(n => JSON.stringify(n)).join('\n'), edges: edges.map(e => JSON.stringify(e)).join('\n') }, A)
    const huge = store(
      [{ id: 'hub', label: 'L'.repeat(5_000), props: { name: 'N'.repeat(120_000) } }, { id: 'a', label: 'L', props: { name: 'leaf-a' } }],
      [{ from: 'hub', type: 't'.repeat(5_000), to: 'a' }],
    )
    const hugeProps = egoOf(huge, 'hub', 'hub', [], 80, 20, true)

    expect(JSON.stringify(hugeProps).length).toBeLessThan(2_000)
    expect([...hugeProps.nodes.map(node => node.name), ...hugeProps.nodes.map(node => node.label), ...hugeProps.groups.map(group => group.title)].every(text => widthOf(text) <= 64)).toBe(true)
    expect(propsFit(hugeProps)).toBe(true)

    const nodes: object[] = [{ id: 'hub', label: 'Epic', props: { name: 'hub' } }]
    const edges: object[] = []
    for (let i = 0; i < 60; i++) {
      nodes.push({ id: `n${i}`, label: 'Issue', props: { name: `${i} ${'w'.repeat(2_000)}` } })
      edges.push({ from: i % 2 === 0 ? 'hub' : `n${i}`, type: `t${i % 7}`, to: i % 2 === 0 ? `n${i}` : 'hub' })
    }
    const wide = egoOf(store(nodes, edges), 'hub', 'hub', [], 120, 30, true)
    expect(wide.nodes.length).toBeGreaterThan(20)
    expect(JSON.stringify(wide).length).toBeLessThan(20_000)
    // Ids are kept whole (the client posts them back): ids that long fall back to text.
    const longIds = egoOf(store([{ id: 'h'.repeat(50_000), label: 'L' }, { id: 'a'.repeat(50_000), label: 'L' }], [{ from: 'h'.repeat(50_000), type: 't', to: 'a'.repeat(50_000) }]), 'h'.repeat(50_000), null, [], 80, 20, true)
    expect(propsFit(longIds)).toBe(false)
  })

  test('allot shares rows by group size, one at least', () => {
    expect(allot([6, 2, 1], 20)).toEqual([6, 2, 1])
    expect(allot([6, 2, 1], 6)).toEqual([4, 1, 1])
    expect(allot([10, 1, 1, 1], 3)).toEqual([1, 1, 1, 0])
  })
})

describe('the browser', () => {
  const act = { tab: () => {}, follow: () => {}, pick: () => {}, label: () => {}, back: () => {}, page: () => {}, exportPatch: () => {} }

  test('columns: the label\'s nodes, then each trail node\'s relations', () => {
    const graph = parseGraph(DIR, AFTER_ADD, A)
    const columns = columnsOf({ graph, trail: ['epc-offline-sync', 'iss-offline-conflict-toast'], browseLabel: null, act })

    expect(columns.map(column => column.title)).toEqual(['Epic 4', 'Offline editing & sync', 'Conflict toast hides unsynced edits'])
    expect(columns[1]?.lines.filter(line => line.kind === 'head').map(line => (line.kind === 'head' ? line.text : ''))).toEqual(
      expect.arrayContaining(['→ contains · Issue 6', '→ owned by · Actor 1', '→ tagged · Label 2', '← specifies · RFC 1']),
    )
    expect(columns[2]?.lines[0]).toEqual({ kind: 'head', text: '← contains · Epic 1' })
    expect(crumbText(graph, ['rfc-offline', 'epc-offline-sync', 'iss-offline-conflict-toast'])).toBe(
      'RFC: Offline editing & conflict resolution › specifies › Offline editing & sync › contains › Conflict toast hides unsynced edits',
    )
    expect(fitStart('RFC: Offline editing › specifies › Offline editing & sync', 30)).toBe('…fies › Offline editing & sync')
  })

  test('a hub with 20,000 relations: its column and its graph are built in well under a second', { timeoutMs: 30_000 }, () => {
    const nodes = [JSON.stringify({ id: 'hub', label: 'Label', props: { name: 'hub' } })]
    const edges: string[] = []
    for (let i = 0; i < 20_000; i++) {
      nodes.push(JSON.stringify({ id: `iss-${i}`, label: 'Issue', props: { name: `Issue number ${(i * 7919) % 20_000}` } }))
      edges.push(JSON.stringify({ from: `iss-${i}`, type: 'tagged', to: 'hub' }))
    }
    const graph = parseGraph(DIR, { manifest: '{"version":"v1"}', nodes: nodes.join('\n'), edges: edges.join('\n') }, A)
    let t = Date.now()
    const columns = columnsOf({ graph, trail: ['hub'], browseLabel: null, act })
    const browse = Date.now() - t
    t = Date.now()
    const props = egoOf(graph, 'hub', 'hub', [], 80, 29, true)
    const drawing = Date.now() - t

    expect(columns[1]?.lines[0]).toEqual({ kind: 'head', text: '← tagged · Issue 20000' })
    expect(columns[1]?.lines[1]).toMatchObject({ kind: 'item', name: 'Issue number 0' })
    expect(props.nodes.find(node => node.more > 0)?.more).toBeGreaterThan(19_000)
    expect({ browse: browse < 400, drawing: drawing < 400 }).toEqual({ browse: true, drawing: true })
  })

  test('a long column keeps the trail\'s line in its window', () => {
    const lines = Array.from({ length: 30 }, (_, i) => ({ kind: 'item' as const, key: `k${i}`, id: `n${i}`, name: `n${i}`, isOnTrail: i === 20, onPress: () => {} }))
    const view = windowOf(lines, 10)

    expect(view.lines).toHaveLength(8)
    expect(view.lines.some(line => line.kind === 'item' && line.isOnTrail)).toBe(true)
    expect(view.above + view.lines.length + view.below).toBe(30)
  })
})
