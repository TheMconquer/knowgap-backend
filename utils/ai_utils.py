# Import necessary libraries
import asyncio
from openai import AsyncOpenAI
from config import Config
# Initialize OpenAI client with your API key
client = AsyncOpenAI(api_key=Config.OPENAI_KEY)

# Define the coroutine for generating core topic with GPT
async def generate_core_topic(question_text, course_name, course_context=""):
    """
    Generate a concise core topic using GPT for a given question.

    Parameters:
    - question_text (str): The text of the question.
    - course_name (str): The name of the course the question belongs to.
    - course_context (str): Additional context for the course, provided by the instructor.

    Returns:
    - str: A concise topic title relevant to the question and course.
    """
    # Set up the prompt
    prompt = (
        f"Based on the following question from course {course_name}, "
        f"generate a concise, specific core topic that is relevant to the subject matter. "
        f"You can assume the course is at a college/university level."
        f"The topic should be no longer than 4-5 words and should directly relate to the main concepts: {question_text}"
    )

    # Append course context if available
    if course_context:
        prompt += f"\nHere's what the instructor gave us, so use it to generate a more relevant topic in the context of the course itself: {course_context}"

    # Prepare message for chat model
    messages = [{"role": "user", "content": prompt}]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            #temperature=0.7,
            max_completion_tokens=10000,
            top_p=0.5
        )
        
        # Extract and clean up the generated topic
        core_topic = response.choices[0].message.content.strip().strip('"').strip("'")
        return {"success": True, "core_topic": core_topic}

    except Exception as e:
        print(f"Error generating core topic: {e}")
        return {"success": False, "error": str(e)}

# Define the coroutine for generating YouTube search topics for a skill
async def generate_skill_search_topics(skill_name, course_name, course_context="", sample_questions=None):
    """
    Generate 1-3 concise YouTube search phrases for a skill using GPT.

    Parameters:
    - skill_name (str): The name of the skill.
    - course_name (str): The name of the course the skill belongs to.
    - course_context (str): Additional context for the course, provided by the instructor or Canvas syllabus.
    - sample_questions (list[str]): A few example quiz questions that test this skill, for extra context.

    Returns:
    - dict: {"success": True, "topics": [str, ...]} or {"success": False, "error": str}
    """
    prompt = (
        f"A student is struggling with the skill \"{skill_name}\" in the course {course_name}. "
        f"Generate 1 to 3 short, specific YouTube search phrases (each no longer than 6 words) "
        f"that would find helpful educational videos teaching this skill at a college/university level. "
        f"Return only the phrases, one per line, with no numbering or extra commentary."
    )

    if course_context:
        prompt += f"\nHere's relevant course context: {course_context}"

    if sample_questions:
        joined_questions = " | ".join(sample_questions[:3])
        prompt += f"\nHere are example quiz questions that test this skill: {joined_questions}"

    messages = [{"role": "user", "content": prompt}]

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_completion_tokens=10000,
            top_p=0.5
        )

        raw_text = response.choices[0].message.content.strip()
        topics = [
            line.strip().strip('"').strip("'").lstrip('-*0123456789. ').strip()
            for line in raw_text.splitlines()
            if line.strip()
        ]
        topics = [t for t in topics if t][:3]

        if not topics:
            return {"success": False, "error": "No topics generated"}

        return {"success": True, "topics": topics}

    except Exception as e:
        print(f"Error generating skill search topics: {e}")
        return {"success": False, "error": str(e)}

# Main block to test the function
if __name__ == "__main__":
    # Define a sample question and course details
    question_text = "Explain the process of photosynthesis in plants."
    course_name = "Biology 101"
    course_context = "Focus on energy conversion in plant cells."

    # Run the test
    async def test_generate_core_topic():
        result = await generate_core_topic(question_text, course_name, course_context)
        if result["success"]:
            print(f"Generated core topic: {result['core_topic']}")
        else:
            print(f"Error: {result['error']}")

    # Execute the test coroutine
    asyncio.run(test_generate_core_topic())
