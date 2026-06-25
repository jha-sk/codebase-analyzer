package model

import (
	"example.com/proj/pkg/util"
)

// User imports util to complete the util <-> model cycle.
type User struct {
	Name string
}

func (u User) Display() string {
	return util.FormatName(u)
}
