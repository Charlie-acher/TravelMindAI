/** 固定接口夹具：运行真实页面与SSE解析；数据只在每个测试内存中保存。 */
import { test as base, expect } from '@playwright/test'
import type { PendingMessage, PlanSnapshot, SavedTurn, TravelRequirement } from '../../src/api/requirements'

const extraction: TravelRequirement = {
  intent: 'plan_trip', destination: '苏州', origin: '上海', days: 2, travelers: 2,
  start_date: '2026-09-25', end_date: '2026-09-26', total_budget: '3000', pace: 'relaxed',
  interests: [], dietary: [], lodging_preferences: [], hard_constraints: [], excluded_items: [], assumptions: [],
}
const itinerary: PlanSnapshot = {
  itinerary_id: 'plan-1', version: 1, previous_version: null, operation: 'create', changes: [], can_undo: false,
  plan: {
    format: 'daily-plan-v1', title: '苏州两日慢游计划', destination: '苏州', warnings: [],
    days: ['拙政园', '留园'].map((name, index) => ({ day: index + 1, date: `2026-09-${25 + index}`,
      activities: [{ start_time: '09:00', duration_minutes: 90, transport: 'walk', transfer_minutes: 0,
        place: { id: `place-${index}`, map: { status: 'not_requested', city: '苏州', name, address: null,
          location: null, provider: 'baidu', checked_at: '2026-09-22T04:00:00Z', poi_id: null, reference_cost: null },
          sources: [{ id: `source-${index}`, kind: 'knowledge', title: '园林旅行参考', text: '完整资料仅在文件预览出现。', url: null }] } }],
    })),
    budget: { days: 2, travelers: 2, nights: 1, rooms: 1, lodging: 'economy', total_budget: '3000',
      price_version: 'demo-v1', unit_prices: {}, costs: { lodging: '200' }, subtotal: '200', contingency_rate: '0.1',
      contingency: '20', total: '220', remaining: '2780', over_budget: false, assumptions: ['测试演示预算'] },
  },
}

type Scenario = { requests: PendingMessage[]; conflict: boolean; unknown: string[] }
export const test = base.extend<{ scenario: Scenario }>({
  scenario: async ({ page }, use) => {
    const scenario: Scenario = { requests: [], conflict: false, unknown: [] }
    const browserErrors: string[] = []
    page.on('pageerror', error => browserErrors.push(error.message))
    let authenticated = false
    let turns: SavedTurn[] = []
    const session = { id: 'session-1', thread_id: 'thread-1', title: '苏州两日慢游计划', status: 'active',
      created_at: '2026-09-22T04:00:00Z', updated_at: '2026-09-22T04:00:00Z' }
    const item = { id: 'plan-1', kind: 'itinerary', file_name: '苏州两日慢游计划.md', mime_type: 'text/markdown',
      size_bytes: null, session_id: session.id, session_title: session.title, created_at: session.created_at, version: 1, version_count: 1 }
    await page.route('**/api/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname, method = request.method()
      const endpoint = `${method} ${path}`
      const json = (body: unknown, status = 200) => route.fulfill({ json: body, status })
      if (endpoint === 'POST /api/v1/auth/login') authenticated = true
      if (endpoint === 'GET /api/v1/auth/me' || endpoint === 'POST /api/v1/auth/login') return authenticated
        ? json({ id: 'browser-user', username: 'browser-user', role: 'user' })
        : json({ error: { message: '请先登录' } }, 401)
      if (endpoint === 'GET /api/v1/requirements/status') return json({ configured: true, model: 'test',
        providers: ['deepseek', 'kimi', 'qwen'].map(id => ({ id, name: { deepseek: 'DeepSeek', kimi: 'Kimi', qwen: 'Qwen' }[id], configured: true })) })
      if (endpoint === 'POST /api/v1/sessions') return json({ session }, 201)
      if (endpoint === 'GET /api/v1/sessions') return json({ items: scenario.requests.length ? [session] : [], next_cursor: null })
      if (endpoint === `GET /api/v1/sessions/${session.id}`) return json({ session })
      if (endpoint === `GET /api/v1/sessions/${session.id}/requirement-messages`) return json({ session_id: session.id, revision: turns.length, turns })
      if (endpoint === `POST /api/v1/sessions/${session.id}/requirement-messages/stream`) {
        const sent = request.postDataJSON() as PendingMessage
        scenario.requests.push(sent)
        if (scenario.conflict) {
          // 模拟另一页面已保存一轮，409后必须真正读到较新历史。
          turns.push({ message_id: 'other-page-message', revision: turns.length + 1, response: {
            ...turns[0].response, itinerary: null, process: null, reply: '另一页面已补充：第二天先休息。',
            result: { ...turns[0].response.result, original_message: '第二天先休息' },
          } })
          return json({ error: { message: '对话已有更新' } }, 409)
        }
        const turn: SavedTurn = { message_id: sent.message_id, revision: turns.length + 1, response: {
          result: { extraction, original_message: sent.message, reference_date: '2026-09-22', missing_required_fields: [], clarification: null, message_intent: 'plan_trip' },
          status: 'complete', reply: '安排好了，每天一座园林，完整攻略已保存到文件。', changed_fields: [], request_id: 'test-request',
          selected_provider: sent.selected_provider, used_providers: [sent.selected_provider ?? 'deepseek'], itinerary,
          process: { seconds: 2, steps: [
            { stage: 'subagent', message: '研究助手', call_id: 'child', status: 'completed' },
            ...Array.from({ length: 5 }, (_, i) => ({ stage: 'knowledge', message: '检索知识库', call_id: `k${i}`, status: 'completed' as const })),
          ] },
        } }
        turns.push(turn)
        return route.fulfill({ contentType: 'text/event-stream', body: `event: done\ndata: ${JSON.stringify(turn)}\n\n` })
      }
      if (endpoint === `GET /api/v1/sessions/${session.id}/workspace-status`) return json({ total_calls: 9, known_total_tokens: 20000, unknown_usage_calls: 1,
        cache_hit_ratio: null, latest_context: { provider: 'qwen', model: 'test-model', input_tokens: 6000, context_window: 1000000,
          ratio: .006, started_at: session.created_at, purpose: 'chat' }, models: [], recent_calls: [], unattributed_history: false })
      if (endpoint === 'GET /api/v1/personal-files') return json({ items: turns.length ? [item] : [], total: turns.length ? 1 : 0, offset: 0, limit: 30 })
      if (endpoint === 'GET /api/v1/personal-files/itinerary/plan-1') return json({ item, itinerary, extraction, attachment: null, versions: [item] })
      // 未声明的接口必须失败，不能偷偷穿过夹具调用本机真实后端。
      scenario.unknown.push(`${method} ${path}`)
      return json({ error: { message: '测试未声明此接口' } }, 501)
    })
    await use(scenario)
    expect(scenario.unknown).toEqual([])
    expect(browserErrors).toEqual([])
  },
})
export { expect }
