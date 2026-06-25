import { capitalize } from "lodash";

import { App } from "./app";
import { User } from "@models/user";

export function bootstrap(): string {
  const app = new App();
  const user: User = { name: "ada" };
  return app.render(capitalize(user.name));
}

export const VERSION = "1.0.0";
