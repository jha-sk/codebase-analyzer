package com.example.service;

import org.apache.commons.lang3.StringUtils;

import com.example.util.StringHelper;

public class Greeter {
    private final StringHelper helper = new StringHelper();

    public String greet(String name) {
        if (StringUtils.isBlank(name)) {
            return "Hello";
        }
        return "Hello, " + helper.capitalize(name);
    }
}
