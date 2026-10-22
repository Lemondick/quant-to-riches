import test from 'node:test'
import assert from 'node:assert/strict'
import { announcementId, sortAnnouncementsDesc } from '../src/services/batch2Api.ts'
import { mockAnnouncements } from '../src/data/batch2.ts'

test('announcements are sorted newest first', () => {
  const sorted = sortAnnouncementsDesc(mockAnnouncements().announcements)
  for (let i = 1; i < sorted.length; i++) {
    assert.ok(sorted[i - 1].published_at >= sorted[i].published_at)
  }
  assert.equal(sorted[0].kind, 'liquidation_risk')
})

test('announcement id falls back to a stable composite key', () => {
  const [first] = mockAnnouncements().announcements
  assert.equal(announcementId(first), `${first.fund_code}|${first.published_at}|${first.title}`)
  assert.equal(announcementId({ ...first, id: 'abc123' }), 'abc123')
})

test('all four kinds are covered by the mock', () => {
  const kinds = new Set(mockAnnouncements().announcements.map((a) => a.kind))
  for (const kind of ['dividend', 'manager_change', 'liquidation_risk', 'other']) {
    assert.ok(kinds.has(kind), `missing kind: ${kind}`)
  }
})
