// Curated Task 2 prompt bank — original prompts written in Cambridge style
// (official Cambridge prompts are copyrighted; do not paste them verbatim).
// v8: this file now only seeds the database on first run (see
// lib/server/store.ts seedPrompt/getPrompts) — the trainer console is the
// live source of truth after that, since prompts can be approved/edited
// there. The original 10 are seeded pre-approved (already in production
// use since v5); the new batch of 15 is seeded UNAPPROVED, pending your
// review in the trainer console before students ever see them.
export interface SeedPrompt {
  id: string;
  topic: string;
  type: string;
  text: string;
  approved: boolean;
}

export const PROMPT_SEED: SeedPrompt[] = [
  // -- approved (from v5, unchanged) -----------------------------------
  {
    id: "env_01",
    topic: "Environment",
    type: "discuss_both_views",
    approved: true,
    text: "Some people think that environmental problems are too big for individuals to solve, while others believe individuals can make a real difference. Discuss both views and give your own opinion.",
  },
  {
    id: "tech_01",
    topic: "Technology",
    type: "advantages_disadvantages",
    approved: true,
    text: "More and more people rely on smartphones for everyday tasks such as banking, navigation, and communication. Do the advantages of this development outweigh the disadvantages?",
  },
  {
    id: "edu_01",
    topic: "Education",
    type: "opinion",
    approved: true,
    text: "Some believe that university education should be free for all students, regardless of their financial background. To what extent do you agree or disagree?",
  },
  {
    id: "age_01",
    topic: "Society & ageing",
    type: "two_questions",
    approved: true,
    text: "In many countries, the proportion of older people is increasing. What problems does this cause for individuals and society? What measures could be taken to address them?",
  },
  {
    id: "work_01",
    topic: "Work",
    type: "discuss_both_views",
    approved: true,
    text: "Some people believe that working from home benefits both employees and employers, while others argue it harms productivity and teamwork. Discuss both views and give your own opinion.",
  },
  {
    id: "health_01",
    topic: "Health",
    type: "problem_solution",
    approved: true,
    text: "The number of people who are overweight is increasing in many countries. What are the causes of this trend, and what solutions can you suggest?",
  },
  {
    id: "gov_01",
    topic: "Government spending",
    type: "opinion",
    approved: true,
    text: "Some people think governments should spend money on public services rather than on the arts. To what extent do you agree or disagree?",
  },
  {
    id: "media_01",
    topic: "Media",
    type: "advantages_disadvantages",
    approved: true,
    text: "Many people now get their news from social media rather than newspapers or television. Is this a positive or negative development?",
  },
  {
    id: "glob_01",
    topic: "Globalization",
    type: "opinion",
    approved: true,
    text: "Some argue that globalization makes cultures around the world increasingly similar, and that this is a negative development. To what extent do you agree or disagree?",
  },
  {
    id: "crime_01",
    topic: "Crime",
    type: "discuss_both_views",
    approved: true,
    text: "Some people believe that longer prison sentences are the best way to reduce crime, while others think there are better alternative methods. Discuss both views and give your own opinion.",
  },

  // -- v8 new batch — pending trainer review/approval ------------------
  {
    id: "urban_01",
    topic: "Urbanization",
    type: "problem_solution",
    approved: false,
    text: "As cities continue to grow rapidly, traffic congestion and housing shortages have become serious problems in many urban areas. What are the causes of these problems, and what steps can be taken to solve them?",
  },
  {
    id: "family_01",
    topic: "Family & parenting",
    type: "opinion",
    approved: false,
    text: "Some people believe that parents should strictly control how much time their children spend on screens and the internet, while others think children should be allowed to make these decisions themselves. To what extent do you agree or disagree?",
  },
  {
    id: "tourism_01",
    topic: "Tourism",
    type: "advantages_disadvantages",
    approved: false,
    text: "Tourism has increased dramatically in many parts of the world over the past few decades. Do the benefits of this growth for local economies outweigh the negative effects on the environment and local communities?",
  },
  {
    id: "sport_01",
    topic: "Sport",
    type: "discuss_both_views",
    approved: false,
    text: "Some people think that professional athletes are paid far too much money, while others argue that their high salaries are justified. Discuss both views and give your own opinion.",
  },
  {
    id: "culture_01",
    topic: "Language & culture",
    type: "two_questions",
    approved: false,
    text: "In some countries, minority languages are gradually disappearing as fewer young people learn to speak them. Why is this happening, and what can be done to preserve these languages?",
  },
  {
    id: "space_01",
    topic: "Science & space",
    type: "opinion",
    approved: false,
    text: "Governments spend large sums of money on space exploration programs. Some people think this money would be better spent solving problems on Earth, such as poverty and disease. To what extent do you agree or disagree?",
  },
  {
    id: "food_01",
    topic: "Food",
    type: "problem_solution",
    approved: false,
    text: "In many countries, a large amount of food is wasted by households, restaurants, and supermarkets every year. What are the main reasons for this, and what measures could reduce food waste?",
  },
  {
    id: "transport_01",
    topic: "Transportation",
    type: "advantages_disadvantages",
    approved: false,
    text: "Some cities have introduced policies to restrict private car use in city centers, encouraging public transport and cycling instead. Do the advantages of such policies outweigh the disadvantages?",
  },
  {
    id: "arts_sci_01",
    topic: "Arts vs. science funding",
    type: "discuss_both_views",
    approved: false,
    text: "Some people believe that governments should prioritize funding for scientific research over the arts, while others think both deserve equal support. Discuss both views and give your own opinion.",
  },
  {
    id: "youth_work_01",
    topic: "Youth unemployment",
    type: "problem_solution",
    approved: false,
    text: "In many countries, a significant number of young people struggle to find employment after finishing their education. What are the causes of youth unemployment, and what can be done to address it?",
  },
  {
    id: "social_rel_01",
    topic: "Social media & relationships",
    type: "opinion",
    approved: false,
    text: "Some people believe that social media has made it easier for people to form and maintain meaningful relationships, while others think it has made relationships more superficial. To what extent do you agree or disagree?",
  },
  {
    id: "renewable_01",
    topic: "Renewable energy",
    type: "two_questions",
    approved: false,
    text: "Many countries are investing heavily in renewable energy sources such as solar and wind power. What are the main reasons for this shift, and what challenges do these countries face in making the transition?",
  },
  {
    id: "wildlife_01",
    topic: "Wildlife conservation",
    type: "opinion",
    approved: false,
    text: "Some people believe that protecting endangered animal species should be a top priority for governments, even if it means limiting economic development in certain areas. To what extent do you agree or disagree?",
  },
  {
    id: "remote_learn_01",
    topic: "Remote learning",
    type: "advantages_disadvantages",
    approved: false,
    text: "Online learning has become increasingly common at schools and universities in recent years. Do the advantages of this method of education outweigh the disadvantages compared to traditional classroom learning?",
  },
  {
    id: "advertising_01",
    topic: "Advertising to children",
    type: "discuss_both_views",
    approved: false,
    text: "Some people think that advertising aimed at children should be banned completely, while others believe parents alone should be responsible for managing what their children see. Discuss both views and give your own opinion.",
  },
];
