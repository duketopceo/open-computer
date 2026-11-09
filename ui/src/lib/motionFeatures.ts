// Motion's DOM features, in their own chunk: the main bundle carries only `LazyMotion` and `m`.
// domMax (not domAnimation) because the timeline uses layout animation when a card opens.
export { domMax as default } from "motion/react";
