import { formatName } from "@/utils";
import { bootstrap } from "./index";

export class App {
  render(name: string): string {
    if (name && name.length > 0) {
      return formatName(name);
    }
    return "empty";
  }

  reboot(): string {
    // Completes the cycle: app -> index -> app.
    return bootstrap();
  }
}
