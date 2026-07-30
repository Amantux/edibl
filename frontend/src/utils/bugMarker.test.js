import { describe, it, expect } from 'vitest'
import { hideMarker, finalizeReply } from './bugMarker'

describe('bugMarker', () => {
  it('finalizeReply strips a complete marker and returns the summary', () => {
    const { content, summary } = finalizeReply('Fix the widget\n[[REPORT_BUG]]')
    expect(content).toBe('Fix the widget')
    expect(summary).toContain('widget')
  })

  it('hideMarker hides a complete or trailing-partial marker while streaming', () => {
    expect(hideMarker('x [[REPO')).toBe('x')          // partial mid-stream → hidden
    expect(hideMarker('done [[REPORT_BUG]]')).toBe('done')
  })

  it('leaves a normal reply untouched with no summary', () => {
    const { content, summary } = finalizeReply('just a normal reply')
    expect(content).toBe('just a normal reply')
    expect(summary).toBeNull()
  })
})
