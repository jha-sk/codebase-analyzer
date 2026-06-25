// CommonJS-style module to exercise require() and .js handling.
const lodash = require("lodash");
const { formatName } = require("./utils");

function legacyGreet(name) {
  return formatName(lodash.capitalize(name));
}

module.exports = { legacyGreet };
