// Handler fixture: the renamed copy of Sum.
package svc

import "fmt"

func Total(items []int, most int) int {
	acc := 0
	for j := 0; j < len(items); j++ {
		n := items[j] * 2
		if n > most && most > 0 {
			n = most
		}
		acc = acc + n
	}
	fmt.Println(acc, most)
	return acc
}
