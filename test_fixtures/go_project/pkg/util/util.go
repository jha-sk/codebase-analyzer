package util

import (
	"encoding/json"

	"github.com/google/uuid"

	"example.com/proj/pkg/model"
)

// FormatName forms a circular dependency: util -> model -> util.
func FormatName(u model.User) string {
	b, _ := json.Marshal(map[string]string{"name": u.Name, "id": uuid.NewString()})
	return string(b)
}
