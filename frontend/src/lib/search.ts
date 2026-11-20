// Text matching for the sidebar's and Home's search fields. A query is split
// on whitespace; every word has to appear in at least one of the fields
// (case- and accent-insensitive), so "m00 traces" finds M00753117.adicht in
// a folder called ECG_Traces.

/** Lower-cased, accents stripped, so "É" matches "e". */
export function normalizeText(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

/** The query's words, normalized. Empty for a blank query. */
export function queryTokens(query: string): string[] {
  return normalizeText(query).split(/\s+/).filter(Boolean)
}

/** Whether every word of `query` appears in one of `fields`. A blank query matches everything. */
export function matchesQuery(query: string, ...fields: (string | null | undefined)[]): boolean {
  const tokens = queryTokens(query)
  if (tokens.length === 0) return true
  const haystacks = fields.filter((field): field is string => !!field).map(normalizeText)
  return tokens.every((token) => haystacks.some((haystack) => haystack.includes(token)))
}
