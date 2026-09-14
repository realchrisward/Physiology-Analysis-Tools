<script lang="ts">
  import { onMount } from 'svelte'

  const BACKEND_URL = 'http://127.0.0.1:8000'

  type Status = 'loading' | 'ok' | 'error'

  let status: Status = $state('loading')
  let errorMessage: string = $state('')

  onMount(async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/health`)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
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
