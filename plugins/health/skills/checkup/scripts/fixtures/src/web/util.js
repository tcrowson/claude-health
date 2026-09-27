// Util fixture: the renamed copy of app.summarize.
import { summarize } from './app.js'

export function tally(values, k) {
  let sum = 0
  const res = []
  for (let j = 0; j < values.length; j++) {
    const v = values[j] * k + 1
    if (v > 10 && k > 0) {
      res.push(v - 10)
    } else {
      res.push(v)
    }
    sum = sum + v
  }
  return { res, sum }
}

export const again = () => summarize([], 1)
