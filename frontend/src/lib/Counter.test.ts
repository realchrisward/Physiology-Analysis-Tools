import { render, screen, fireEvent } from '@testing-library/svelte'
import { describe, expect, it } from 'vitest'
import Counter from './Counter.svelte'

describe('Counter', () => {
  it('increments when clicked', async () => {
    render(Counter)
    const button = screen.getByRole('button')
    expect(button).toHaveTextContent('Count is 0')

    await fireEvent.click(button)

    expect(button).toHaveTextContent('Count is 1')
  })
})
