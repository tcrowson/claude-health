// App fixture: a renamed copy of util.tally, an empty catch, a long function, a cycle with util.js.
import { tally } from './util.js'

export function summarize(items, factor) {
  let total = 0
  const out = []
  for (let i = 0; i < items.length; i++) {
    const value = items[i] * factor + 1
    if (value > 10 && factor > 0) {
      out.push(value - 10)
    } else {
      out.push(value)
    }
    total = total + value
  }
  return { out, total }
}

export function load(url) {
  try {
    return fetch(url)
  } catch (err) {}
  return tally
}

export function longOne(x) {
  let y = x
  y = y + 1
  y = y + 2
  y = y + 3
  y = y + 4
  y = y + 5
  y = y + 6
  y = y + 7
  y = y + 8
  y = y + 9
  y = y + 10
  y = y + 11
  y = y + 12
  y = y + 13
  y = y + 14
  y = y + 15
  y = y + 16
  y = y + 17
  y = y + 18
  y = y + 19
  y = y + 20
  y = y + 21
  y = y + 22
  y = y + 23
  y = y + 24
  y = y + 25
  y = y + 26
  y = y + 27
  y = y + 28
  y = y + 29
  y = y + 30
  y = y + 31
  y = y + 32
  y = y + 33
  y = y + 34
  y = y + 35
  y = y + 36
  y = y + 37
  y = y + 38
  y = y + 39
  y = y + 40
  y = y + 41
  y = y + 42
  y = y + 43
  y = y + 44
  y = y + 45
  y = y + 46
  y = y + 47
  y = y + 48
  y = y + 49
  y = y + 50
  y = y + 51
  y = y + 52
  y = y + 53
  y = y + 54
  y = y + 55
  y = y + 56
  y = y + 57
  y = y + 58
  y = y + 59
  y = y + 60
  y = y + 61
  y = y + 62
  return y
}
