/** Public copy guard for the A-leggo Next product shell. */
/** Single user-facing version label for the whole product shell. */
export const APP_DISPLAY_VERSION = "A-leggo V4";
/** True when a URL, name, or command exposes the former runtime brand. */
export function isUpstreamArtifact(text?: string | null): boolean {
  return /deeptutor/i.test(text ?? "");
}

export function brandText(value: string): string {
  return value
    .replace(/\bA-leggo\b(?!\s+Next)/g, "A-leggo Next")
    .replace(/(?:https?:\/\/)?(?:docs\.)?deeptutor\.info\S*/gi, "Local documentation")
    .replace(/DeepTutor/gi, "A-leggo Next")
    .replace(/\bMastery Path\b/g, "Review & Mastery")
    .replace(/\bImmersive Reading\b/g, "Textbook Reading")
    .replace(/\bLearning Space\b/g, "Study Records")
    .replace(/\bMy Agents\b/g, "Smart Tools")
    .replace(/\bCo-Writer\b/g, "Study Notes")
    .replace(/\bKnowledge Center\b/g, "Materials Library")
    .replace(/\bPartners\b/g, "Learning Companion")
    .replace(/\bBook\b/g, "Living Book")
    .replace(/\bHome\b/g, "Solve & Review");
}
