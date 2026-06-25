import { User } from "@models/user";

export function formatName(name: string): string {
  return JSON.stringify({ name } as Partial<User>);
}

export const PREFIX = "user:";
