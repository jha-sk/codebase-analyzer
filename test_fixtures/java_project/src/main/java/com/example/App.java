package com.example;

import java.util.List;
import java.util.ArrayList;

import com.example.service.Greeter;

public class App {
    public static void main(String[] args) {
        Greeter greeter = new Greeter();
        List<String> names = new ArrayList<>();
        names.add("ada");
        for (String name : names) {
            if (name != null && !name.isEmpty()) {
                System.out.println(greeter.greet(name));
            }
        }
    }
}
