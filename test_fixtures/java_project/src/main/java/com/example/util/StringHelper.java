package com.example.util;

import com.example.service.Greeter;

// Completes the Greeter <-> StringHelper cycle.
public class StringHelper {
    public String capitalize(String value) {
        if (value == null || value.isEmpty()) {
            return value;
        }
        return Character.toUpperCase(value.charAt(0)) + value.substring(1);
    }

    public Greeter newGreeter() {
        return new Greeter();
    }
}
