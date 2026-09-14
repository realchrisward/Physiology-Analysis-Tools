<script lang="ts">
  import { onMount } from 'svelte'
  import { getBackendUrl } from './lib/api'

  type HealthResponse = { status: string }

  type Status = 'loading' | 'ok' | 'error'

  let status: Status = $state('loading')
  let errorMessage: string = $state('')

  onMount(async () => {
    try {
      const backendUrl = await getBackendUrl()
      const response = await fetch(`${backendUrl}/health`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data: HealthResponse = await response.json()
      status = data.status === 'ok' ? 'ok' : 'error'
    } catch (e) {
      status = 'error'
      errorMessage = e instanceof Error ? e.message : String(e)
    }
  })
</script>

{#if status === 'loading'}
  <p data-testid="health-status">Checking backend...</p>
{:else if status === 'ok'}
  <p data-testid="health-status">Backend: ok</p>
{:else}
  <p data-testid="health-status">Backend: error{errorMessage ? ` (${errorMessage})` : ''}</p>
{/if}
