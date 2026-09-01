# services/achieveup_ai_service.py

import logging
import json
import re
import os
from typing import List, Dict, Any
from openai import AsyncOpenAI
from datetime import datetime, timezone
from config import Config

# Set up logging
logger = logging.getLogger(__name__)

# OpenAI configuration
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY') or Config.OPENAI_KEY

GENERIC_SKILLS = [
    'Critical Thinking', 'Problem Solving', 'Analytical Reasoning', 'Communication',
    'Research Skills', 'Time Management', 'Collaboration', 'Technical Writing'
]

async def analyze_questions(questions_data: List[Dict[str, Any]], course_skills: List[str] = None) -> List[Dict[str, Any]]:
    """
    Analyze questions for complexity and skill mapping using batching for efficiency.
    """
    if not course_skills:
        course_skills = GENERIC_SKILLS
        
    results = []
    
    # 1. Initial pass: Calculate complexity and identify questions needing AI
    needs_ai = []
    for i, question in enumerate(questions_data):
        complexity = analyze_question_complexity(question)
        question_text = question.get('question_text', '') or question.get('text', '')
        
        # Start with keyword matching
        suggested_skills = classify_question_skills_keywords(question_text, course_skills)
        
        # If keywords didn't find clear matches, queue for AI
        if not suggested_skills or len(suggested_skills) == 0:
            needs_ai.append(i)
            
        results.append({
            'questionId': question.get('id'),
            'complexity': complexity,
            'suggestedSkills': suggested_skills,
            'confidence': 0.5, # Default confidence for keyword matching
            'analysis_timestamp': datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        })

    # 2. Batch AI processing (if enabled and needed)
    if OPENAI_API_KEY and needs_ai:
        batch_size = 20
        for i in range(0, len(needs_ai), batch_size):
            chunk_indices = needs_ai[i : i + batch_size]
            chunk_texts = [
                (questions_data[idx].get('question_text', '') or questions_data[idx].get('text', ''))
                for idx in chunk_indices
            ]
            
            logger.info(f"Processing AI batch for {len(chunk_texts)} questions...")
            batch_results = await classify_questions_batch_ai(chunk_texts, course_skills)
            
            if batch_results:
                for j, idx in enumerate(chunk_indices):
                    # batch_results is expected to be a dict or list mapping index to skills
                    ai_skills = batch_results.get(str(j)) or batch_results.get(j)
                    if ai_skills:
                        results[idx]['suggestedSkills'] = ai_skills
                        results[idx]['confidence'] = 0.85 # Higher confidence for AI

    # 3. If no skills
    for res in results:
        if not res['suggestedSkills']:
            res['confidence'] = 0.0 # Set confidence to 0 since no skills found

    return results

def analyze_question_complexity(question: Dict[str, Any]) -> str:
    """Analyze question complexity based on text content and structure."""
    question_text = question.get('question_text', '') or question.get('text', '')
    points = question.get('points', 1)
    
    # Text-based indicators
    text_length = len(question_text)
    word_count = len(question_text.split())
    
    # Complexity indicators
    high_complexity_words = [
        'analyze', 'evaluate', 'compare', 'contrast', 'synthesize', 'justify', 'critique',
        'design', 'implement', 'optimize', 'debug', 'troubleshoot', 'architect'
    ]
    
    medium_complexity_words = [
        'explain', 'describe', 'apply', 'demonstrate', 'solve', 'calculate',
        'identify', 'classify', 'interpret', 'predict'
    ]
    
    low_complexity_words = [
        'define', 'list', 'name', 'recall', 'state', 'recognize', 'select', 'choose'
    ]
    
    question_lower = question_text.lower()
    
    # Count complexity indicators
    high_count = sum(1 for word in high_complexity_words if word in question_lower)
    medium_count = sum(1 for word in medium_complexity_words if word in question_lower)
    low_count = sum(1 for word in low_complexity_words if word in question_lower)
    
    # Scoring system
    complexity_score = 0
    
    # Text length and word count
    if word_count > 50:
        complexity_score += 2
    elif word_count > 25:
        complexity_score += 1
    
    # Points value
    if points >= 10:
        complexity_score += 2
    elif points >= 5:
        complexity_score += 1
    
    # Keyword analysis
    complexity_score += high_count * 3
    complexity_score += medium_count * 2
    complexity_score -= low_count * 1
    
    # Question type analysis
    if any(phrase in question_lower for phrase in ['multiple choice', 'select all', 'true/false']):
        complexity_score -= 1
    elif any(phrase in question_lower for phrase in ['essay', 'explain', 'describe in detail']):
        complexity_score += 2
    
    # Final classification
    if complexity_score >= 6:
        return 'high'
    elif complexity_score >= 3:
        return 'medium'
    else:
        return 'low'

async def classify_questions_batch_ai(question_texts: List[str], available_skills: List[str]) -> Dict[str, List[str]]:
    """Use OpenAI to classify multiple questions at once for efficiency."""
    if not OPENAI_API_KEY or not question_texts:
        return {}
    
    try:
        # Prepare numbered list of questions
        questions_formatted = "\n".join([f"{i}. {text[:500]}" for i, text in enumerate(question_texts)])
        
        prompt = f"""
        Map each of the following {len(question_texts)} questions to 1-3 most relevant skills from the provided list.
        
        Available Skills: {', '.join(available_skills)}
        
        Questions:
        {questions_formatted}
        
        Return ONLY a valid JSON object where the keys are the question numbers (as strings "0", "1", etc.) and values are arrays of skill names.
        Example:
        {{
            "0": ["Skill A", "Skill B"],
            "1": ["Skill C"]
        }}
        """

        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at mapping educational content to learning skills. Output only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        batch_results = json.loads(content)
        
        # Clean and validate
        cleaned_results = {}
        for key, skills in batch_results.items():
            if isinstance(skills, list):
                valid_skills = [s for s in skills if s in available_skills]
                cleaned_results[key] = valid_skills[:3]
        
        return cleaned_results

    except Exception as e:
        logger.error(f"Batch AI skill classification error ({type(e).__name__}): {str(e)}")
        return {}


def classify_question_skills_keywords(question_text: str, available_skills: List[str]) -> List[str]:
    """Classify question skills using keyword matching."""
    question_lower = question_text.lower()
    skill_scores = {}
    
    for skill in available_skills:
        score = 0
        skill_lower = skill.lower()
        skill_words = skill_lower.split()
        
        # Direct skill name match
        if skill_lower in question_lower:
            score += 10
        
        # Individual word matches
        for word in skill_words:
            if len(word) > 3 and word in question_lower:
                score += 3
        
        # Related keyword matching
        skill_keywords = get_skill_keywords(skill)
        for keyword in skill_keywords:
            if keyword in question_lower:
                score += 2
        
        if score > 0:
            skill_scores[skill] = score

    # Return top 3 skills by score
    sorted_skills = sorted(skill_scores.items(), key=lambda x: x[1], reverse=True)
    top_skills = [skill for skill, score in sorted_skills[:3]]

    return top_skills

def get_skill_keywords(skill: str) -> List[str]:
    """Get related keywords for a skill."""
    skill_keyword_map = {
        'Programming Fundamentals': ['code', 'program', 'algorithm', 'function', 'variable', 'loop'],
        'HTML/CSS Fundamentals': ['html', 'css', 'web', 'tag', 'style', 'markup'],
        'JavaScript Programming': ['javascript', 'js', 'script', 'dom', 'event', 'function'],
        'Database Management': ['database', 'sql', 'query', 'table', 'data', 'select'],
        'Network Protocols': ['network', 'protocol', 'tcp', 'ip', 'http', 'packet'],
        'Software Testing': ['test', 'testing', 'debug', 'error', 'bug', 'validation'],
        'Algorithm Design': ['algorithm', 'efficiency', 'complexity', 'optimization', 'sort', 'search'],
        'Data Structures': ['array', 'list', 'tree', 'graph', 'stack', 'queue', 'hash'],
        'Object-Oriented Programming': ['class', 'object', 'inheritance', 'polymorphism', 'encapsulation'],
        'Problem Solving': ['solve', 'solution', 'approach', 'strategy', 'analyze', 'reasoning']
    }
    
    return skill_keyword_map.get(skill, [])

async def bulk_assign_skills(course_id: str, quiz_id: str, questions: List[Dict[str, Any]], course_skills: List[str]) -> Dict[str, List[str]]:
    """Perform bulk skill assignment for all questions in a quiz."""
    try:
        assignments = {}
        
        # Analyze all questions
        question_analyses = await analyze_questions(questions, course_skills)
        
        # Extract skill assignments
        for analysis in question_analyses:
            question_id = analysis.get('questionId')
            suggested_skills = analysis.get('suggestedSkills', [])
            
            # Only assign skills with reasonable confidence
            if analysis.get('confidence', 0) >= 0.5 and suggested_skills:
                assignments[question_id] = suggested_skills
            else:
                assignments[question_id] = []
        
        logger.info(f"Bulk assigned skills to {len(assignments)} questions in quiz {quiz_id}")
        return assignments
        
    except Exception as e:
        logger.error(f"Bulk skill assignment error: {str(e)}")
        return {} 