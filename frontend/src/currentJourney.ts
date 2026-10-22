import type { PlanningResultCard, ResultCard, RouteResultCard, TransitResultCard } from './api'

export type RouteChoiceCard = PlanningResultCard | TransitResultCard | RouteResultCard
export type JourneyMessage = { id: string; cards?: ResultCard[] }
export type CurrentJourney = {
  threadId: string
  card: RouteChoiceCard
}

export function isRouteChoiceCard(card: ResultCard): card is RouteChoiceCard {
  return card.type === 'planning' || card.type === 'transit' || card.type === 'route'
}

export function latestJourney(messages: JourneyMessage[], threadId: string): CurrentJourney | null {
  if (!threadId) return null
  for (const message of [...messages].reverse()) {
    const cards = message.cards ?? []
    for (let index = cards.length - 1; index >= 0; index--) {
      if (isRouteChoiceCard(cards[index])) {
        return { threadId, card: cards[index] as RouteChoiceCard }
      }
    }
  }
  return null
}

export function restoreJourney(
  cached: CurrentJourney | null | undefined, messages: JourneyMessage[], threadId: string,
): CurrentJourney | null {
  if (threadId && cached?.threadId === threadId && cached.card && isRouteChoiceCard(cached.card)) return cached
  return latestJourney(messages, threadId)
}

export function journeySummary(card: RouteChoiceCard): string {
  const modes = { driving: '驾车', transit: '公共交通', walking: '步行' }
  if (card.type === 'planning') {
    const choice = card.route_candidates?.find((candidate) => candidate.plan_id === card.selected_plan_id)
    const selected = choice?.title ?? modes[card.selected_mode ?? card.recommended_mode]
    return `${card.origin_name} → ${card.destination_name}的方案已更新。系统推荐${modes[card.recommended_mode]}，当前选择${selected}。`
      + (card.used_stale_snapshot ? '当前使用过期快照，出发前请刷新确认。' : '')
  }
  return card.type === 'transit' ? '公共交通方案已更新，可在当前行程中查看和选择具体路线。'
    : `${modes[card.mode]}路线已生成，路线详情与地图已放入当前行程。`
}
