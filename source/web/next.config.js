const path = require("path");
module.exports = {
  output: "export",
  images: { unoptimized: true },
  webpack: (config) => { config.resolve.alias.cytoscape = path.resolve(__dirname, "node_modules/cytoscape/dist/cytoscape.cjs.js"); return config; },
};
