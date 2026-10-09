import { describe, expect, test } from 'claude-code/testing'

import { summaryOf } from '../hooks/register'
import { ATTACH, BAND, HEADLESS, PANE, RUN, SESSION, SUBAGENT_TURN, TURN, worldOf } from './fixtures'

describe('where nothing draws', () => {
  test('claude -p starts it inert: no command, no band, no counting', async ($, on) => {
    const world = worldOf(on, [])

    await $.session.start(HEADLESS)
    await $.turn.complete(TURN)

    expect(world.commands).toEqual([])
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('/mod-template before session.start or in claude -p is passed on and opens nothing', async ($, on) => {
    const world = worldOf(on, [])

    expect(await $.command.run(RUN)).toEqual({ text: 'Claude Code ran /mod-template' })
    await $.session.start(HEADLESS)
    expect(await $.command.run(RUN)).toEqual({ text: 'Claude Code ran /mod-template' })

    expect(world.opened).toEqual([])
  })

  test('a client attaching later brings it live once', async ($, on) => {
    const world = worldOf(on, [])
    const band = await $.ui.mount({ ...BAND, surface: 'desktop' })

    await $.session.start(HEADLESS)
    expect(await $.session.attach(ATTACH)).toEqual({ clientId: 'desktop:default' })
    await $.session.attach({ ...ATTACH, surface: 'mobile', clientId: 'mobile:default' })
    await $.turn.complete(TURN)

    expect(world.commands).toEqual([{ name: 'mod-template', immediate: true }])
    expect(await band.find({ type: 'Text', text: 'mod-template · 1 turn · last 12s' })).toBeDefined()
  })
})

describe('observe-only', () => {
  test('session.start and turn.complete come back as the engine answered them', async ($, on) => {
    worldOf(on)

    expect(await $.session.start(SESSION)).toEqual({ cwd: '/work' })
    expect(await $.turn.complete(TURN)).toEqual({ text: '' })
  })

  test('a refused command name leaves the session starting, the band drawing and /mod-template to its owner', async ($, on) => {
    const world = worldOf(on)

    world.refuseCommand = '"/mod-template" refused: taken'
    expect(await $.session.start(SESSION)).toEqual({ cwd: '/work' })

    expect(world.commands).toEqual([])
    expect(world.logs.join('\n')).toMatch(/not registered/)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: 'mod-template · no turns yet' })).toBeDefined()
    expect(await $.command.run(RUN)).toEqual({ text: 'Claude Code ran /mod-template' })
    expect(world.opened).toEqual([])
  })
})

describe('the command and the pane', () => {
  test('registers /mod-template to run mid-turn', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    expect(world.commands).toEqual([{ name: 'mod-template', immediate: true }])
  })

  test('/mod-template opens a focused pane that Esc closes, and prints nothing', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    expect(await $.command.run(RUN)).toEqual({})
    expect(world.opened).toEqual([{ id: 'mod-template', focus: true, closeOnEscape: true }])
  })

  test('/mod-template says why when the pane waits', async ($, on) => {
    const world = worldOf(on)

    world.isPlaced = false
    await $.session.start(SESSION)

    expect(await $.command.run(RUN)).toEqual({ text: 'The pane is waiting: unasked below 144 columns' })
  })

  test('the pane counts main-loop turns only, on the terminal and the desktop', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.turn.complete(TURN)
    await $.turn.complete(SUBAGENT_TURN)
    await $.turn.complete(TURN)

    for (const surface of ['terminal', 'desktop'] as const) {
      const pane = await $.ui.mount({ ...PANE, surface })

      expect(await pane.find({ type: 'Text', text: 'This session: 2 turns · last 12s' })).toBeDefined()
      await pane.press({ key: 'close' })
      await pane.unmount()
    }

    expect(world.closed).toEqual(['mod-template', 'mod-template'])
  })

  test('the pane toggles the band', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    await pane.press({ key: 'band' })
    expect(await pane.find({ key: 'band' })).toMatchObject({ props: { label: 'Show band' } })
    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()

    await pane.press({ key: 'band' })
    expect(await pane.find({ key: 'band' })).toMatchObject({ props: { label: 'Hide band' } })
    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeDefined()
  })
})

describe('the band above the prompt', () => {
  test('shows as soon as the session starts, though the prompt drew before it', async ($, on) => {
    worldOf(on)

    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()

    await $.session.start(SESSION)

    expect(await band.find({ type: 'Text', text: 'mod-template · no turns yet' })).toBeDefined()
  })

  test('draws one line over what the mods after it draw', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: 'mod-template · no turns yet' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('redraws after a turn without being asked: $.state subscribed it', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'desktop' })

    await $.turn.complete(TURN)

    expect(await band.find({ type: 'Text', text: 'mod-template · 1 turn · last 12s' })).toBeDefined()
  })

  test('hide takes it down and leaves the others drawing', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    await band.press({ key: 'hide' })

    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('a survey holds the band', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, hasSurvey: true }, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()
  })

  test('the band option set to false leaves the band to the others', { options: { band: false } }, async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /mod-template/ })).toBeUndefined()
  })
})

test('summaryOf words the count and the last turn', () => {
  expect(summaryOf(0, null)).toBe('no turns yet')
  expect(summaryOf(1, 4_400)).toBe('1 turn · last 4s')
  expect(summaryOf(3, null)).toBe('3 turns')
})
