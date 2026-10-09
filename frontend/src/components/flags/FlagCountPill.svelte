<script lang="ts">
  import type { FlagKind } from '../../lib/api/types'
  import { FLAG_KIND_COLOR_VAR, FLAG_KIND_LABEL, FLAG_KIND_SOFT_VAR, FLAG_KIND_TITLE } from '../../lib/flags'

  // "11 FP" / "3 false negatives": how many flags of one kind a list or
  // recording holds, coloured like the same flags on the graph.
  let { kind, count, long = false }: { kind: FlagKind; count: number; long?: boolean } = $props()

  const LONG_LABEL: Record<FlagKind, [string, string]> = {
    fp: ['false positive', 'false positives'],
    fn: ['false negative', 'false negatives'],
    flag: ['flagged beat', 'flagged beats'],
  }

  let label = $derived(long ? LONG_LABEL[kind][count === 1 ? 0 : 1] : FLAG_KIND_LABEL[kind])
</script>

<span
  class="flag-pill"
  data-testid={`flag-pill-${kind}`}
  title={FLAG_KIND_TITLE[kind]}
  style:color={`var(${FLAG_KIND_COLOR_VAR[kind]})`}
  style:background={`var(${FLAG_KIND_SOFT_VAR[kind]})`}>{count} {label}</span
>

<style>
  .flag-pill {
    display: inline-block;
    padding: 1px var(--space-2);
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    line-height: 1.5;
    white-space: nowrap;
  }
</style>
