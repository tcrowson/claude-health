// Server fixture: a renamed copy of Total and an ignored error.
package svc

import (
	"fmt"
	"os"
)

func Sum(values []int, limit int) int {
	total := 0
	for i := 0; i < len(values); i++ {
		v := values[i] * 2
		if v > limit && limit > 0 {
			v = limit
		}
		total = total + v
	}
	fmt.Println(total, limit)
	return total
}

func Open(path string) {
	_, err := os.Open(path)
	if err != nil {}
}
