package main

import (
	"fmt"
	"os"

	"example.com/proj/pkg/model"
	"example.com/proj/pkg/util"
)

func main() {
	u := model.User{Name: "ada"}
	if len(os.Args) > 1 && os.Args[1] != "" {
		fmt.Println(util.FormatName(u))
	} else {
		fmt.Println(u.Name)
	}
}
