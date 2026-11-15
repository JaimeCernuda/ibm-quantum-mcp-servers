import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// One self-contained HTML file: the server serves it as each of its four views and the
// page loads nothing from the network (each resource declares an empty CSP).
export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: "../../ibm-quantum-mcp-server/src/ibm_quantum_mcp_server/views",
    emptyOutDir: false,
    target: "es2022",
    cssCodeSplit: false,
    assetsInlineLimit: 100_000_000,
    rollupOptions: { input: "quantum-views.html" },
  },
});
